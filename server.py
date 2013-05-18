import sys

from autobahn.websocket import listenWS

from twisted.internet import reactor, ssl
import twisted.internet.protocol as twistedsockets
from twisted.python import log
from rpi_ws.server_protocol import RPIServerProtocol, RPISocketServerFactory, SiteComm, FlashSocketPolicyServerProtocol
from twisted.web import server

from etc import local_settings

DEBUG = False

PROVIDEFLASHSOCKETPOLICYFILE = True


def main():
    if local_settings.WS_USE_SSL or local_settings.WS_HTTP_SSL:
        contextFactory = ssl.DefaultOpenSSLContextFactory('certs/server.key',
                                                          'certs/server.crt')

    if local_settings.WS_USE_SSL:
        uri_type = "wss"
    else:
        uri_type = "ws"

    server_url = "%s://%s:%d" % (uri_type, local_settings.WS_SERVER_IP, local_settings.WS_PORT)

    if DEBUG:
        log.startLogging(sys.stdout)

    factory = RPISocketServerFactory(server_url, debug=DEBUG, debugCodePaths=DEBUG)
    factory.protocol = RPIServerProtocol

    sitecomm = SiteComm(factory)
    factory.sitecomm = sitecomm
    site = server.Site(sitecomm)

    if local_settings.WS_USE_SSL:
        listenWS(factory, contextFactory)
    else:
        listenWS(factory)

    if local_settings.WS_HTTP_SSL:
        reactor.listenSSL(local_settings.WS_HTTP_PORT, site, contextFactory)
    else:
        reactor.listenTCP(local_settings.WS_HTTP_PORT, site)

 #   if PROVIDEFLASHSOCKETPOLICYFILE:
 #       socketfactory = twistedsockets.Factory()
 #       socketfactory.protocol = FlashSocketPolicyServerProtocol
 #       reactor.listenTCP(843, socketfactory)

    reactor.run()


if __name__ == '__main__':
    main()
