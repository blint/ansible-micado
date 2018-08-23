from  Zorp.Core import  *
from  Zorp.Proxy import  *
from  Zorp.Http import  *

Zorp.firewall_name = 'zorp-test@micado-master'
config.options.kzorp_enabled = FALSE

Zone(name='internet',
     addrs=[
     '0.0.0.0/0',
]
)

class AnyURLResponseHttpProxy(HttpProxy):
    def config(self):
        super(AnyURLResponseHttpProxy, self).config()
        self.max_keepalive_requests = 1
        self.request["GET"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.request["POST"] = (HTTP_REQ_POLICY, self.reqRedirect)
        self.request["PUT"] = (HTTP_REQ_POLICY, self.reqRedirect)

    def reqRedirect(self, method, url, version):
        self.custom_response_body = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" 
"http://www.w3.org/TR/html4/loose.dtd">
<html>
<head>
<title>test</title>
</head>
<body>
<p style="font-family:helvetica; font-size:9pt;">This page intentionally left blank</p>
</body>
</html>"""
        self.error_status = 200
        self.error_msg = "OK"
        self.error_headers = "Content-Type: text/html\r\n"
        return HTTP_REQ_CUSTOM_RESPONSE

def default() :
    Service(name='interHTTP', router=DirectedRouter(dest_addr=(SockAddrInet('127.0.0.1', 4000)), overrideable=TRUE), chainer=ConnectChainer(), proxy_class=AnyURLResponseHttpProxy, max_instances=0, max_sessions=0, keepalive=Z_KEEPALIVE_NONE)
    Dispatcher(transparent=FALSE, bindto=DBIface(protocol=ZD_PROTO_TCP, port=80, iface="eth0", family=2), rule_port="80", service="interHTTP")
    Dispatcher(transparent=FALSE, bindto=DBIface(protocol=ZD_PROTO_TCP, port=9090, iface="eth0", family=2), rule_port="9090", service="interHTTP")
    Dispatcher(transparent=FALSE, bindto=DBIface(protocol=ZD_PROTO_TCP, port=8080, iface="eth0", family=2), rule_port="8080", service="interHTTP")
    Dispatcher(transparent=FALSE, bindto=DBIface(protocol=ZD_PROTO_TCP, port=3000, iface="eth0", family=2), rule_port="3000", service="interHTTP")
    Dispatcher(transparent=FALSE, bindto=DBIface(protocol=ZD_PROTO_TCP, port=5050, iface="eth0", family=2), rule_port="5050", service="interHTTP")
