import sys

from twisted.internet import reactor
from twisted.python import log
from autobahn.websocket import WebSocketClientFactory,\
    WebSocketClientProtocol,\
    connectWS
from rpi_ws.client_protocol import RPIClientProtocol, ReconnectingWebSocketClientFactory
from rpi_ws import settings

DEBUG = True

def main():
    if settings.WS_HTTP_SSL:
        uri_type = "wss"
    else:
        uri_type = "ws"

    server_url = "%s://%s:%d/rpi/" % (uri_type, settings.WS_SERVER_IP, settings.WS_PORT)

    if DEBUG:
        log.startLogging(sys.stdout)

    #factory = WebSocketClientFactory(server_url, useragent=settings.RPI_USER_AGENT, debug=DEBUG)
    factory = ReconnectingWebSocketClientFactory(server_url, useragent=settings.RPI_USER_AGENT, debug=DEBUG)
    factory.protocol = RPIClientProtocol

    connectWS(factory)
    reactor.run()


if __name__ == '__main__':
    main()