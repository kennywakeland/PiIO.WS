# SPI adc by blaisejarrett
#from RPiBJ import SPIADC
# GPIO: http://code.google.com/p/raspberry-gpio-python/
from RPi import GPIO
from glob import glob
from time import sleep
import utility


class CHPortInUseException(Exception):
    pass


class CHPortDoesntExistException(Exception):
    pass


class IBase(object):
    IO_TYPE_BINARY = 'B'
    IO_TYPE_INTEGER = 'I'

    # default state, override this
    IO_TYPE = IO_TYPE_BINARY
    # override this, stored value, description
    # stored value must be an integer, desc must be str
    # ex: ((1, 'GPIO1'),)
    IO_CHOICES = (())

    ALLOW_DUPLICATE_PORTS = False
    channels_in_use = {}

    def __init__(self, ch_port):
        self.ch_port = ch_port

        port_exists = False
        for existing_port, existing_port_name in self.IO_CHOICES:
            if str(existing_port) == str(ch_port):
                port_exists = True
                break

        if not port_exists:
            raise CHPortDoesntExistException('Port %s does not exist' % ch_port)

        if not self.__class__.ALLOW_DUPLICATE_PORTS:
            print "self.__class__.channels_in_use :", self.__class__.channels_in_use
            if ch_port in self.__class__.channels_in_use:
                raise CHPortInUseException("Channel %s is in use" % ch_port)

            self.__class__.channels_in_use[ch_port] = self

    @classmethod
    def flush(cls):
        """
        Clean out all instances
        """
        for key, value in cls.channels_in_use.items():
            value.close()

    def close(self):
        """
        Because we're probably dealing with IO
        """
        del self.__class__.channels_in_use[self.ch_port]

    @classmethod
    def open(cls, *args, **kwargs):
        """
        Open constructor, idea is to provide a File like interface
        """
        return cls(*args, **kwargs)


class IRead(IBase):
    """
    Interface you should extend to define interfaces that are
    available to poll for data on the RPI.
    """

    def read(self):
        """
        Poll for new data
        Blocks until new data becomes available.
        Returns data
        """
        raise NotImplementedError("Should have implemented this")

    def __iter__(self):
        """
        Generator to repeatedly poll
        """
        while True:
            yield self.read()


class IWrite(IBase):
    """
    Interface you should extend to implement a rpi writable interface
    """
    # this is the default value assumed when no data has been written
    DEFAULT_VALUE = None

    def __init__(self, ch_port):
        super(IWrite, self).__init__(ch_port)
        self.last_written_value = self.DEFAULT_VALUE

    def read(self):
        """
        By default returns the last written state, If no write calls have been made
        it returns the value set by DEFAULT_VALUE
        """
        return self.last_written_value

    def write(self, value):
        self.last_written_value = value


# SPIADC.setup(0, 100000)
#
#
# class ADC(IRead):
#     """
#     Maps to ADC using library
#     Read only implied
#     """
#     IO_TYPE = IBase.IO_TYPE_INTEGER
#     # we're using an 8 channel ADC
#     IO_CHOICES = (
#         (0, 'CH0'),
#         (1, 'CH1'),
#         (2, 'CH2'),
#         (3, 'CH3'),
#         (4, 'CH4'),
#         (5, 'CH5'),
#         (6, 'CH6'),
#         (7, 'CH7'),
#     )
#
#     class ChannelInUseError(Exception): pass
#
#     channels_in_use = {}
#
#     def __init__(self, ch_port):
#         super(ADC, self).__init__(ch_port)
#
#     def read(self):
#         return SPIADC.read(self.ch_port)

def temperature_io_choices():
    base_dir = '/sys/bus/w1/devices/'
    io_choices = (())

    for device_folder in glob(base_dir + '28*'):
        device_file = device_folder + '/w1_slave'
        io_choices += ((device_file,device_file),)

    return io_choices


class Temperature(IRead):

    IO_TYPE = IBase.IO_TYPE_INTEGER
    channels_in_use = {}
    IO_CHOICES = temperature_io_choices()

    def __init__(self, ch_port):
        super(Temperature, self).__init__(ch_port)

    def read(self):
        return self.read_temp(self.ch_port)

    def read_temp_raw(self, device_file):
        f = open(device_file, 'r')
        lines = f.readlines()
        f.close()
        return lines

    def read_temp(self, device_file):
        temp_raw = self.read_temp_raw(device_file)

        count_max_wile = 0
        while temp_raw[0].strip()[-3:] != 'YES' and temp_raw[1].find('t=') != -1:
            sleep(0.6)
            temp_raw = self.read_temp_raw(device_file)
            count_max_wile += 1
            if count_max_wile > 2:
                raise

        temp_string = temp_raw[1][temp_raw[1].find('t=') + 2:]
        temp_string = temp_string.replace("\n", "").replace(" ", "")
        return temp_string



class MODIOInput(IRead):
    """
    Maps to MOD-IO read only
    """

    address = 0x58
    IO_TYPE = IBase.IO_TYPE_BINARY
    IO_CHOICES = (
        (4, 'Relay 1'),
        (3, 'Relay 2'),
        (2, 'Relay 3'),
        (1, 'Relay 4'),
        (20, 'Digital input 1'),
        (21, 'Digital input 2'),
        (22, 'Digital input 3'),
        (23, 'Digital input 4'),
        (30, 'Analogue inputs 1'),
        (31, 'Analogue inputs 2'),
        (32, 'Analogue inputs 3'),
        (33, 'Analogue inputs 4'),
    )

    channels_in_use = {}

    def read(self):
        ch_port = int(self.ch_port)
        if 1 <= ch_port <= 4:
            return utility.RELAY.get_state(ch_port)
        elif 20 <= ch_port <= 23:
            return utility.DIGITAL_INPUT.get_state(ch_port - 20)
        elif 30 <= ch_port <= 33:
            return 0
        else:
            return 0


class MODIOOutput(IWrite):
    """
    Maps to MOD-IO write
    """
    IO_TYPE = IBase.IO_TYPE_BINARY
    IO_CHOICES = (
        (4, 'Relay 1'),
        (3, 'Relay 2'),
        (2, 'Relay 3'),
        (1, 'Relay 4'),
    )
    DEFAULT_VALUE = False

    def write(self, value):
        utility.RELAY.set_state(self.ch_port, int(value))
        super(MODIOOutput, self).write(value)


GPIO.setmode(GPIO.BCM)


class GPIOInput(IRead):
    """
    Maps to GPIO read only
    """
    IO_TYPE = IBase.IO_TYPE_BINARY
    IO_CHOICES = (
        (2, 'GPIO2 P3'),
        (3, 'GPIO3 P5'),
        (4, 'GPIO4 P7'),
        (7, 'GPIO7 P26'),
        (8, 'GPIO8 P24'),
        (9, 'GPIO9 P21'),
        (10, 'GPIO10 P19'),
        (11, 'GPIO11 P23'),
        (14, 'GPIO14 P8'),
        (15, 'GPIO15 P10'),
        (17, 'GPIO17 P11'),
        (18, 'GPIO18 P12'),
        (22, 'GPIO22 P15'),
        (23, 'GPIO23 P16'),
        (24, 'GPIO24 P18'),
        (25, 'GPIO25 P22'),
        (27, 'GPIO27 P13'),
    )

    ports_in_use = {}

    def __init__(self, ch_port):
        super(GPIOInput, self).__init__(ch_port)
        GPIO.setup(ch_port, GPIO.IN)

    def read(self):
        """
        Note: GPIO reads should be faster then network IO, careful of poll rate
        """
        return GPIO.input(self.ch_port)


class GPIOOutput(IWrite):
    """
    Maps to GPIO write
    """
    IO_TYPE = IBase.IO_TYPE_BINARY
    IO_CHOICES = (
        (2, 'GPIO2 P3'),
        (3, 'GPIO3 P5'),
        (4, 'GPIO4 P7'),
        (7, 'GPIO7 P26'),
        (8, 'GPIO8 P24'),
        (9, 'GPIO9 P21'),
        (10, 'GPIO10 P19'),
        (11, 'GPIO11 P23'),
        (14, 'GPIO14 P8'),
        (15, 'GPIO15 P10'),
        (17, 'GPIO17 P11'),
        (18, 'GPIO18 P12'),
        (22, 'GPIO22 P15'),
        (23, 'GPIO23 P16'),
        (24, 'GPIO24 P18'),
        (25, 'GPIO25 P22'),
        (27, 'GPIO27 P13'),
    )
    DEFAULT_VALUE = False

    def __init__(self, ch_port):
        super(GPIOOutput, self).__init__(ch_port)
        GPIO.setup(ch_port, GPIO.OUT)

    def write(self, value):
        if value is True:
            GPIO.output(self.ch_port, GPIO.HIGH)
        elif value is False:
            GPIO.output(self.ch_port, GPIO.LOW)
        else:
            # not a boolean value
            # throw?
            return
        super(GPIOOutput, self).write(value)


def get_interface_desc():
    read_cls = IRead.__subclasses__()
    write_cls = IWrite.__subclasses__()

    ret = {}
    ret['read'] = read_cls
    ret['write'] = write_cls
    return ret

