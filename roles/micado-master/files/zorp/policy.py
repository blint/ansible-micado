from  Zorp.Core import  *
from  Zorp.Proxy import  *
from  Zorp.Http import  *
from  Zorp.AnyPy import  *
from  Zorp.Auth import  *
from  Zorp.AuthDB import *
from  Zorp.FileLock import *
from  Zorp.Cache import *
from  Zorp.Stream import *
import socket
import base64

Zorp.firewall_name = 'zorp@micado-master'
config.options.kzorp_enabled = FALSE

Zone(name='internet',
     addrs=[
     '0.0.0.0/0',
]
)

EncryptionPolicy(
    name="https_clientonly_encryption_policy",
    encryption=ClientOnlyEncryption(
        client_verify=ClientNoneVerifier(),
        client_ssl_options=ClientSSLOptions(
            method=SSL_METHOD_ALL,
            cipher="ECDH+AESGCM:DH+AESGCM:ECDH+AES256:DH+AES256:ECDH+AES128:DH+AES:!aNULL:!MD5:!DSS",
            cipher_server_preference=FALSE,
            timeout=300,
            disable_sslv2=TRUE,
            disable_sslv3=TRUE,
            disable_tlsv1=TRUE,
            disable_tlsv1_1=TRUE,
            disable_tlsv1_2=FALSE,
            disable_compression=FALSE
        ),
        client_certificate_generator=StaticCertificate(
            certificate=Certificate.fromFile(
                certificate_file_path="/etc/zorp/ssl.pem",
                private_key=PrivateKey.fromFile("/etc/zorp/ssl.key")
            )
        )
    )
)

class HtpasswdAuthenticationBackend(AbstractAuthenticationBackend):
    def __init__(self, filename):
        from passlib.apache import HtpasswdFile

        self.ht = HtpasswdFile(filename)
        self.sessions = {}

    def startSession(self, session_id, session):
        pass

    def stopSession(self, session_id):
        del self.sessions[session_id]

    def getMethods(self, session_id, entity):
        user = None
        for (headername, value) in entity:
            if headername == "User":
                user = value
        if not user:
            log(session_id, CORE_AUTH, 1, "Could not parse user, rejecting;")
            return Z_AUTH_REJECT
        else:
            if user not in self.ht.users():
                 log(session_id, CORE_AUTH, 1, "User not found in htpass file;")
                 return Z_AUTH_REJECT
            self.sessions[session_id] = user
        return (2, [('Method', 'PASSWD.NONE:0:0:Password Authentication/htpasswd')])

    def setMethod(self, session_id, method):
        return (4, [])

    def converse(self, session_id, credentials):
        passwd = None
        for (method, cred) in credentials:
            if method == "Password":
                passwd = cred
        if not passwd:
            log(session_id, CORE_AUTH, 1, "Could not parse password, rejecting;")
            return Z_AUTH_REJECT
        else:
            user = self.sessions[session_id]
            if self.ht.verify(user, passwd):
                 log(session_id, CORE_AUTH, 1, "Accepted authentication; user='%s'", (user,))
                 return Z_AUTH_ACCEPT
            else:
                 log(session_id, CORE_AUTH, 1, "Wrong password; user='%s'", (user,))
                 return Z_AUTH_REJECT

class PersistentTimedCache(TimedCache):
    def __init__(self, filename, timeout, update_stamp=TRUE, cleanup_threshold=100):
        super(PersistentTimedCache, self).__init__(filename, timeout, update_stamp, cleanup_threshold)
        import os
        if not os.path.isfile(filename):
            try:
                self.clear()
            except IOError, e:
                log(None, CORE_ERROR, 1, "Error creating persistent cache file; filename='%s', error='%s'", (filename, str(e)))
        else:
            self.cleanup()
            self.read_persistent_cache()

    def read_persistent_cache(self):
        import cPickle as pickle
        with FileLock("%s.lock" % self.name):
            picklefile = open(self.name, "rb")
            self.cache = dict(pickle.load(picklefile))
            picklefile.close()

    def persist_cache(self):
        import cPickle as pickle
        with FileLock("%s.lock" % self.name):
            picklefile = open(self.name, "wb")
            pickle.dump(self.cache, picklefile)
            picklefile.close()

    def cleanup(self):
        super(PersistentTimedCache, self).cleanup()
        self.persist_cache()

    def lookup(self, key):
        self.read_persistent_cache()
        return super(PersistentTimedCache, self).lookup(key)

    def store(self, key, value):
        self.read_persistent_cache()
        super(PersistentTimedCache, self).store(key, value)
        self.persist_cache()

    def remove(self, key):
        self.read_persistent_cache()
        if self.cache.has_key(key):
            del self.cache[key]
        self.persist_cache()

    def clear(self):
        super(PersistentTimedCache, self).clear()
        self.persist_cache()

class SessionHttpProxy(HttpProxy):

    session_cache = PersistentTimedCache("/var/lib/zorp/tmp/http_session_cache", 600, TRUE)

    def config(self):
        super(SessionHttpProxy, self).config()
        #self.request_header["Cookie"] = (HTTP_HDR_POLICY, self.processCookies)
        self.request["GET"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.request["POST"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.request["PUT"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.response["*", "*"] = (HTTP_RSP_POLICY, self.respRedirect)
        #self.request_stack["*"] = (HTTP_STK_MIME, (Z_STACK_PROXY, HttpSessionAnyPy))
        #self.response_stack["*"] = (HTTP_STK_MIME, (Z_STACK_PROXY, HttpSessionAnyPy))
        self.http_session_cookie_name = "ZorpSession"

    def __post_config__(self):
        super(SessionHttpProxy, self).__post_config__()
        self.http_session_add_cookie_header = FALSE
        self.http_session_id = None
        self.http_session_expired = FALSE
        self.http_session_data = {}

    def generateClientKey(self):
        import os
        import base64
        random_bytes = os.urandom(32)
        encoded_random_bytes = base64.b64encode(random_bytes)
        return encoded_random_bytes

    def addSessionCookie(self):
        setcookie_header = self.getResponseHeader("Set-Cookie")
        new_header = ""
        if setcookie_header:
            new_header = setcookie_header + "\r\nSet-Cookie: "
        #this should be handled more sophisticated on the C side of the code
        new_header = "%s=%s;  path=/; domain=%s" % (self.http_session_cookie_name, self.http_session_id, self.request_url_host, )
        self.setResponseHeader("Set-Cookie", new_header)

    def createHttpSession(self):
        proxyLog(self, HTTP_POLICY, 6, "Creating new HTTP session; session_id='%s'", (self.http_session_id,))
        self.http_session_add_cookie_header = TRUE
        self.http_session_id = self.generateClientKey()
        self.http_session_data = {"session_start": time.time()}
        proxyLog(self, HTTP_POLICY, 6, "Saving HTTP session; id='%s', data='%s'", (self.http_session_id, self.http_session_data,))
        self.session_cache.store(self.http_session_id, self.http_session_data)

    def processCookies(self, value):
        import Cookie
        cookie = Cookie.SimpleCookie()
        cookie.load(value)
        if cookie.has_key(self.http_session_cookie_name):
            proxyLog(self, HTTP_POLICY, 7, "Session cache state; state='%s'", (self.session_cache.cache,))
            self.http_session_data = self.session_cache.lookup(cookie[self.http_session_cookie_name].value)
            if self.http_session_data:
                self.http_session_id = cookie[self.http_session_cookie_name].value
            proxyLog(self, HTTP_POLICY, 1, "Session id; id='%s'", (self.http_session_id,))

    def reqRedirect(self, method, url, version):
        import time
        proxyLog(self, HTTP_POLICY, 6, "Cookie header; header='%s'", (self.getRequestHeader("Cookie"),))
        cookie_header = self.getRequestHeader("Cookie")
        if cookie_header:
            self.processCookies(cookie_header)
        if self.http_session_id is None:
            self.createHttpSession()
        else:
            proxyLog(self, HTTP_POLICY, 1, "Yeehaw, we have a session; session_id='%s'", (self.http_session_id,))
        return HTTP_REQ_ACCEPT

    def respRedirect(self, method, url, version, response):
        if self.http_session_add_cookie_header:
            self.addSessionCookie()
        proxyLog(self, HTTP_POLICY, 6, "Saving HTTP session; id='%s', data='%s'", (self.http_session_id, self.http_session_data,))
        self.session_cache.store(self.http_session_id, self.http_session_data)
        return HTTP_RSP_ACCEPT

class FormAuthParseLoginProxy(AnyPyProxy):
    def config(self):
        self.client_max_line_length=16384
        self.auth = HtpasswdAuthenticationBackend("/etc/zorp/users.htpass")

    def __post_config__(self):
        super(FormAuthParseLoginProxy, self).__post_config__()
        self.encryption = Globals.none_encryption

    def parseAuthForm(self, data):
        import urlparse
        import urllib
        formdata = urlparse.parse_qs(data, True, True)
        if not formdata.has_key("username") or not formdata.has_key("password") or not formdata.has_key("redirect_location"):
            raise ValueError, "Invalid form parameters supplied; formdata='%s'" % formdata
        redirect_location = urllib.unquote(formdata["redirect_location"][0])
        proxyLog(self, HTTP_POLICY, 1, "Redirect location; loc='%s'", (redirect_location,))
        return (formdata["username"][0], formdata["password"][0], redirect_location)

    def setUnauthorizedVerdict(self, redirect_location):
        self.session.http.error_status = 301
        self.session.http.error_status = "Unauthorized, redirecting to login page"
        self.session.http.error_headers = "Location: %s\r\n" % redirect_location
        self.set_verdict(ZV_REJECT, "Invalid login data")

    def setAuthorizedVerdict(self, username, redirect_location):
        # replace with this once GPL userAuthenticated bug is resolved
        #self.session.proxy.userAuthenticated(username, 'inband')
        self.session.getMasterSession().auth_user = username
        self.session.auth_info = 'inband'

        self.session.http.http_session_data["auth_username"] = username
        self.session.http.session_cache.store(self.session.http.http_session_id, self.session.http.http_session_data)

        self.session.http.error_status = 301
        self.session.http.error_status = "You are now authenticated"
        self.session.http.error_headers = "Location: %s\r\n" % redirect_location
        self.session.http.error_headers += "Set-Cookie: %s=%s;  path=/; domain=%s\r\n" % (self.session.http.http_session_cookie_name, self.session.http.http_session_id, self.session.http.request_url_host, )
        self.set_verdict(ZV_REJECT, "User authenticated")

    def proxyThread(self):
        while 1:
            try:
                line=self.client_stream.readline()
                (username, password, redirect_location) = self.parseAuthForm(line)
                if self.auth.getMethods(self.session.session_id, [("User", username)]) != Z_AUTH_REJECT:
                    self.auth.setMethod(self.session.session_id, "PASSWD.NONE:0:0:Password Authentication/inband")
                    if self.auth.converse(self.session.session_id, [("Password", password)]) != Z_AUTH_REJECT:
                        return self.setAuthorizedVerdict(username, redirect_location)
                    else:
                        return self.setUnauthorizedVerdict(redirect_location)
                else:
                    return self.setUnauthorizedVerdict(redirect_location)
            except StreamException, (code, line):
                if code == G_IO_STATUS_EOF:
                    return
                else:
                    raise

            self.set_verdict(ZV_REJECT, "Invalid state")
            return

class FormAuthHttpProxy(SessionHttpProxy):

    def config(self):
        super(FormAuthHttpProxy, self).config()
        self.max_keepalive_requests = 1
        self.request["GET"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.request["POST"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.request["PUT"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.response["*", "*"] = (HTTP_RSP_POLICY, self.respRedirect)
        self.http_session_cookie_name = "ZorpSession"

    def __post_config__(self):
        super(FormAuthHttpProxy, self).__post_config__()

    def getAuthForm(self, url):
        import urllib
        f = open("usr/share/zorp/http/en/authform.html", 'r')
        authform = f.read()
        f.close()
        authform = authform.replace('name="redirect_location" value=""', 'name="redirect_location" value="%s"' % (urllib.quote(url),))
        return authform

    def showAuthForm(self, url):
        self.custom_response_body = self.getAuthForm(url)
        self.error_status = 200
        self.error_msg = "OK"
        self.error_headers = "Content-Type: text/html\r\n"
        return HTTP_REQ_CUSTOM_RESPONSE

    def reqRedirect(self, method, url, version):
        ancestor_verdict = super(FormAuthHttpProxy, self).reqRedirect(method, url, version)
        if ancestor_verdict != HTTP_REQ_ACCEPT:
            return ancestor_verdict

        proxyLog(self, HTTP_POLICY, 1, "Every request up to here")

        if not self.http_session_data.has_key("auth_username"):

            proxyLog(self, HTTP_POLICY, 1, "No auth_username here; method='%s', url='%s', version='%s'", (method, url, version, ))

            if method == "POST":
                self.request_stack["POST"] = (HTTP_STK_DATA, (Z_STACK_PROXY, FormAuthParseLoginProxy))
                return HTTP_REQ_ACCEPT
            else:
                return self.showAuthForm(url)

        proxyLog(self, HTTP_POLICY, 1, "Only authenticated requests should get here")

        if self.request_url_file.startswith("/logout"):
            proxyLog(self, HTTP_POLICY, 1, "Destroying session; id='%s'", (self.http_session_id,))
            self.session_cache.remove(self.http_session_id)
            self.error_status = 301
            self.error_msg = "Logged out"
            self.error_info = "You are now logged out"
            self.error_headers = "Location: %s\r\n" % self.request_url.replace("/logout", "")
            return HTTP_REQ_REJECT

        return HTTP_REQ_ACCEPT

    def respRedirect(self, method, url, version, response):
        ancestor_verdict = super(FormAuthHttpProxy, self).respRedirect(method, url, version, response)
        if ancestor_verdict != HTTP_RSP_ACCEPT:
            return ancestor_verdict
        if self.http_session_data.has_key("auth_username") and not self.request_url_file.startswith("/logout"):
            return HTTP_RSP_ACCEPT
        else:
            return HTTP_RSP_REJECT

class MicadoMasterHttpProxy(SessionHttpProxy):
    def __pre_config__(self):
        self.urlmapping = {}
        return super(MicadoMasterHttpProxy, self).__pre_config__()

    def config(self):
        super(MicadoMasterHttpProxy, self).config()
        self.rewrite_host_header = FALSE
        self.max_keepalive_requests = 1
        self.error_silent = TRUE
        self.request["GET"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.request["POST"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.request["PUT"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.response_header["Strict-Transport-Security"] = (HTTP_HDR_REPLACE, "max-age=63072000; includeSubdomains;")
        self.urlmapping["/submitter"] = ("micado-submitter", 5000, True)
        self.urlmapping["/prometheus"] = ("prometheus", 9090, False)
        self.urlmapping["/alertmanager"] = ("alertmanager", 9093, True)
        self.urlmapping["/docker-visualizer"] = ("dockervisualizer", 8080, False)
        self.urlmapping["/grafana"] = ("grafana", 3000, True)
        self.urlmapping["/dashboard"] = ("micado-dashboard", 4000, True)
        self.urlmapping["/toscasubmitter"] = ("toscasubmitter", 5050, True)

    def reqRedirect(self, method, url, version):
        proxyLog(self, HTTP_POLICY, 3, "Got URL %s", (url,))
        if url.startswith("http://"):
            self.error_status = 301
            self.error_headers="Location: %s\n" % url.replace("http://", "https://")
            return HTTP_REQ_REJECT
        return HTTP_REQ_ACCEPT

    def setServerAddress(self, host, port):
        for path in self.urlmapping.keys():
            if self.request_url_file.startswith(path):
                (container, port, remove_prefix_on_server_side) = self.urlmapping[path]
                proxyLog(self, HTTP_POLICY, 3, "Mapping url; path='%s', container='%s', port='%s'", (path, container, port))
                self.setRequestHeader("Host", container+":"+str(port))
                if remove_prefix_on_server_side:
                    import urlparse
                    newpath = self.request_url_file[len(path)+1:]
                    if newpath == "":
                        newpath = "/"
                    self.request_url = urlparse.urlunsplit((self.request_url_proto, self.request_url_host+":"+str(port), newpath, self.request_url_query, None))
                    proxyLog(self, HTTP_POLICY, 3, "Mapping to new url; url='%s'", (self.request_url))
                return HttpProxy.setServerAddress(self, socket.gethostbyname(container), port)
        return HttpProxy.setServerAddress(self, socket.gethostbyname("micado-dashboard"), 4000)

def default() :
    Service(name='interHTTPS', router=DirectedRouter(dest_addr=(SockAddrInet('127.0.0.1', 80)), overrideable=FALSE), chainer=ConnectChainer(), proxy_class=FormAuthHttpProxy, max_instances=0, max_sessions=0, keepalive=Z_KEEPALIVE_NONE, encryption_policy="https_clientonly_encryption_policy")
    Dispatcher(transparent=FALSE, bindto=DBIface(protocol=ZD_PROTO_TCP, port=443, iface="eth0", family=2), rule_port="443", service="interHTTPS")
    Service(name='interHTTP', router=DirectedRouter(dest_addr=(SockAddrInet('127.0.0.1', 80),)), chainer=ConnectChainer(), proxy_class=MicadoMasterHttpProxy, max_instances=0, max_sessions=0, keepalive=Z_KEEPALIVE_NONE)
    Dispatcher(transparent=FALSE, bindto=DBIface(protocol=ZD_PROTO_TCP, port=80, iface="eth0", family=2), rule_port="80", service="interHTTP")