import json
from twisted.internet import reactor
from twisted.python import log
from twisted.web import resource
import urllib
import urllib2
from rpi_ws import settings

__author__ = 'kenny'


class SiteComm(resource.Resource):
    """
    To handle requests from the website
    """
    isLeaf = True

    def __init__(self, ws_factory, *args, **kwargs):
        resource.Resource.__init__(self, *args, **kwargs)

        self.ws_factory = ws_factory

    def render_GET(self, request):
        request.setHeader("Content-Type", "application/json")
        return "%s" % (str(self.ws_factory.rpi_clients),)

    def render_POST(self, request):
        # should be called to update configs by admin change

        request.setHeader("Content-Type", "application/json")

        try:
            rpis = json.loads(request.args['json'][0])

            for rpi in rpis:
                if self.ws_factory.debug:
                    log.msg('render_POST - Received config for RPI %s' % rpi['mac'])
        except:
            if self.ws_factory.debug:
                log.err('render_POST -  Error parsing rpi configs')
            return 'error'

        # delegate request to the WS factory
        self.ws_factory.config_rpi(rpi)
        #self.ws_factory.

        return 'ok'

    def register_rpi(self, rpi):
        # we need mac, ip, interface desc
        payload = {'mac': rpi.mac,
                   'ip': rpi.protocol.peer.host,
                   'inter_face': rpi.inter_face}

        post_data = {'json': json.dumps(payload)}
        post_data = urllib.urlencode(post_data)
        try:
            url = urllib2.Request('http://%s/ws_comm/register/' % settings.SITE_SERVER_ADDRESS, post_data)
            url_response = urllib2.urlopen(url)
            url_response.read()
        except:
            pass
            # TODO: success validation

        # notify users
        reactor.callFromThread(self.ws_factory.register_rpi_wsite, rpi)
        # register should return configs

    def disconnect_rpi(self, rpi):
        payload = {'mac': rpi.mac}

        post_data = {'json': json.dumps(payload)}
        post_data = urllib.urlencode(post_data)
        try:
            url = urllib2.Request('http://%s/ws_comm/disconnect/' % settings.SITE_SERVER_ADDRESS, post_data)
            url_response = urllib2.urlopen(url)
            url_response.read()
        except:
            pass

        # notify users
        reactor.callFromThread(self.ws_factory.disconnect_rpi_wsite, rpi)