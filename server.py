import sys

from autobahn.websocket import listenWS
from twisted.internet import reactor, ssl
import twisted.internet.protocol as twistedsockets
from twisted.python import log
from rpi_ws.server.site_comm import SiteComm
from rpi_ws.server_protocol import RPIServerProtocol, RPISocketServerFactory
from twisted.web import server

from rpi_ws import settings

DEBUG = False #True

PROVIDEFLASHSOCKETPOLICYFILE = True


def main():
    if settings.WS_USE_SSL or settings.WS_HTTP_SSL:
        contextFactory = ssl.DefaultOpenSSLContextFactory('certs/server.key',
                                                          'certs/server.crt')

    if settings.WS_USE_SSL:
        uri_type = "wss"
    else:
        uri_type = "ws"

    server_url = "%s://%s:%d" % (uri_type, settings.WS_SERVER_IP, settings.WS_PORT)

    if DEBUG:
        log.startLogging(sys.stdout)

    factory = RPISocketServerFactory(server_url, debug=DEBUG, debugCodePaths=DEBUG)
    factory.protocol = RPIServerProtocol

    sitecomm = SiteComm(factory)
    factory.sitecomm = sitecomm
    site = server.Site(sitecomm)

    if settings.WS_USE_SSL:
        listenWS(factory, contextFactory)
    else:
        listenWS(factory)

    if settings.WS_HTTP_SSL:
        reactor.listenSSL(settings.WS_HTTP_PORT, site, contextFactory)
    else:
        reactor.listenTCP(settings.WS_HTTP_PORT, site)

 #   if PROVIDEFLASHSOCKETPOLICYFILE:
 #       socketfactory = twistedsockets.Factory()
 #       socketfactory.protocol = FlashSocketPolicyServerProtocol
 #       reactor.listenTCP(843, socketfactory)

    reactor.run()


if __name__ == '__main__':
    main()
