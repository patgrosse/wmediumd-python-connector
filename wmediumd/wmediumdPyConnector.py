"""
Helps starting the wmediumd service

author: Patrick Grosse (patrick.grosse@uni-muenster.de)
"""
import ctypes
import os
import pkg_resources
import socket
import tempfile
import subprocess
import signal
import time
import struct
import stat
from logging import debug, info, error


class WmediumdConstants:
    def __init__(self):
        raise Exception("WmediumdConstants cannot be initialized")
        pass

    WMEDIUMD_MODE_SNR = 0
    WMEDIUMD_MODE_ERRPROB = 1
    WMEDIUMD_MODE_SPECPROB = 2

    WSERVER_SHUTDOWN_REQUEST_TYPE = 0
    WSERVER_SNR_UPDATE_REQUEST_TYPE = 1
    WSERVER_SNR_UPDATE_RESPONSE_TYPE = 2
    WSERVER_DEL_BY_MAC_REQUEST_TYPE = 3
    WSERVER_DEL_BY_MAC_RESPONSE_TYPE = 4
    WSERVER_DEL_BY_ID_REQUEST_TYPE = 5
    WSERVER_DEL_BY_ID_RESPONSE_TYPE = 6
    WSERVER_ADD_REQUEST_TYPE = 7
    WSERVER_ADD_RESPONSE_TYPE = 8
    WSERVER_ERRPROB_UPDATE_REQUEST_TYPE = 9
    WSERVER_ERRPROB_UPDATE_RESPONSE_TYPE = 10
    WSERVER_SPECPROB_UPDATE_REQUEST_TYPE = 11
    WSERVER_SPECPROB_UPDATE_RESPONSE_TYPE = 12

    WUPDATE_SUCCESS = 0
    WUPDATE_INTF_NOTFOUND = 1
    WUPDATE_INTF_DUPLICATE = 2
    WUPDATE_WRONG_MODE = 3

    SOCKET_PATH = '/var/run/wmediumd.sock'
    LOG_PREFIX = 'wmediumd:'


class WmediumdException(Exception):
    pass


class WmediumdManager(object):
    is_connected = False
    registered_interfaces = []

    @classmethod
    def connect(cls, uds_address=WmediumdConstants.SOCKET_PATH, mode=WmediumdConstants.WMEDIUMD_MODE_SNR):
        # type: (str) -> None
        """
        Connect to the wmediumd server

        :param uds_address: The Unix Domain socket path
        :param mode: wmediumd mode to use if the server is not started
        :type uds_address str
        :type mode int
        """
        h = int(subprocess.check_output("lsmod | grep 'mac80211_hwsim' | wc -l",
                                        shell=True))
        if h == 0:
            os.system('modprobe mac80211_hwsim radios=0')
        is_started = False
        h = int(subprocess.check_output("pgrep -x \'wmediumd\' | wc -l",
                                        shell=True))
        if h > 0:
            if os.path.exists(uds_address) and stat.S_ISSOCK(os.stat(uds_address).st_mode):
                is_started = True
            else:
                raise WmediumdException("wmediumd is already started but without server")
        if not is_started:
            WmediumdStarter.initialize(parameters=['-l', '5'], mode=mode)
            WmediumdStarter.start_managed()
            time.sleep(1)
        WmediumdServerConn.connect(uds_address)
        cls.is_connected = True

    @classmethod
    def disconnect(cls):
        # type: () -> None
        """
        Disconnect from the wmediumd server and unregister all registered interfaces
        """
        if not cls.is_connected:
            raise WmediumdException("WmediumdConnector is not connected to wmediumd")
        registered_interfaces = cls.registered_interfaces[:]
        for mac in registered_interfaces:
            cls.unregister_interface(mac)
        WmediumdServerConn.disconnect()
        cls.is_connected = False

    @classmethod
    def register_interface(cls, mac):
        # type: (str) -> int
        """
        Register a new interface at wmediumd
        :param mac The mac address of the interface
        :return The wmediumd station index

        :type mac: str
        :rtype int
        """
        ret = WmediumdServerConn.register_interface(mac)
        cls.registered_interfaces.append(mac)
        return ret

    @classmethod
    def unregister_interface(cls, mac):
        # type: (str) -> None
        """
        Unregister a station at wmediumd
        :param mac The mac address of the interface

        :type mac: str
        """
        WmediumdServerConn.unregister_interface(mac)
        cls.registered_interfaces.remove(mac)

    @classmethod
    def update_link_snr(cls, link):
        # type: (WmediumdSNRLink) -> None
        """
        Update the SNR of a connection at wmediumd
        :param link The link to update

        :type link: WmediumdSNRLink
        """
        WmediumdServerConn.update_link_snr(link)

    @classmethod
    def update_link_errprob(cls, link):
        # type: (WmediumdERRPROBLink) -> None
        """
        Update the error probability of a connection at wmediumd
        :param link The link to update

        :type link: WmediumdERRPROBLink
        """
        WmediumdServerConn.update_link_errprob(link)

    @classmethod
    def update_link_specprob(cls, link):
        # type: (WmediumdSPECPROBLink) -> None
        """
        Update the error probability matrix of a connection at wmediumd
        :param link The link to update

        :type link: WmediumdSPECPROBLink
        """
        WmediumdServerConn.update_link_specprob(link)


class WmediumdStarter(object):
    is_managed = False

    is_initialized = False
    intfrefs = None
    links = None
    executable = None
    parameters = None
    auto_add_links = None
    default_auto_snr = None

    is_connected = False
    wmd_process = None
    wmd_config_name = None
    wmd_logfile = None
    default_auto_errprob = 0.0
    mode = 0

    @classmethod
    def initialize(cls, intfrefs=None, links=None, executable='wmediumd', with_server=True, parameters=None,
                   auto_add_links=True, default_auto_snr=-10, default_auto_errprob=1.0,
                   mode=WmediumdConstants.WMEDIUMD_MODE_SNR):
        """
        Set the data for the wmediumd daemon

        :param intfrefs: A list of all WmediumdIntfRef that should be managed in wmediumd
        :param links: A list of WmediumdLink
        :param executable: The wmediumd executable
        :param with_server: True if the wmediumd server should be started
        :param parameters: Parameters to pass to the wmediumd executable
        :param auto_add_links: If true, it will add all missing links pairs with the default_auto_snr as SNR
        :param default_auto_snr: The default SNR
        :param default_auto_errprob: The default error probability
        :param mode: WmediumdConstants.WMEDIUMD_MODE_* constant

        :type intfrefs: list of WmediumdIntfRef
        :type links: list of WmediumdLink
        """
        if intfrefs is None:
            intfrefs = []
        if links is None:
            links = []
        if parameters is None:
            parameters = ['-l', '4']
        if with_server:
            parameters.append('-s')
        cls.intfrefs = intfrefs
        cls.links = links
        cls.executable = executable
        cls.parameters = parameters
        cls.auto_add_links = auto_add_links
        cls.default_auto_snr = default_auto_snr
        cls.default_auto_errprob = default_auto_errprob
        cls.mode = mode
        if mode != WmediumdConstants.WMEDIUMD_MODE_SNR and mode != WmediumdConstants.WMEDIUMD_MODE_ERRPROB \
                and mode != WmediumdConstants.WMEDIUMD_MODE_SPECPROB:
            raise Exception("Wrong wmediumd mode given")
        cls.is_initialized = True

    @classmethod
    def start(cls):
        """
        Start wmediumd

        Notice: The stations can reach each other before this method is called and
        some scripts may use some kind of a cache (eg. iw station dump)
        """
        if not cls.is_initialized:
            raise WmediumdException("Use WmediumdStarter.initialize first to set the required data")

        if cls.is_connected:
            raise WmediumdException("WmediumdStarter is already connected")

        mappedintfrefs = {}
        mappedlinks = {}

        # Map all links using the interface identifier and check for missing interfaces in the  intfrefs list
        for link in cls.links:
            link_id = link.sta1intfref.identifier() + '/' + link.sta2intfref.identifier()
            mappedlinks[link_id] = link
            found1 = False
            found2 = False
            for intfref in cls.intfrefs:
                if link.sta1intfref.get_station_name() == intfref.get_station_name():
                    if link.sta1intfref.get_station_name() == intfref.get_station_name():
                        found1 = True
                if link.sta2intfref.get_station_name() == intfref.get_station_name():
                    if link.sta2intfref.get_station_name() == intfref.get_station_name():
                        found2 = True
            if not found1:
                raise WmediumdException('%s is not part of the managed interfaces' % link.sta1intfref.identifier())
                pass
            if not found2:
                raise WmediumdException('%s is not part of the managed interfaces' % link.sta2intfref.identifier())

        if cls.mode != WmediumdConstants.WMEDIUMD_MODE_SPECPROB:
            # Auto add links
            if cls.auto_add_links:
                for intfref1 in cls.intfrefs:
                    for intfref2 in cls.intfrefs:
                        if intfref1 != intfref2:
                            link_id = intfref1.identifier() + '/' + intfref2.identifier()
                            if cls.mode == WmediumdConstants.WMEDIUMD_MODE_ERRPROB:
                                mappedlinks.setdefault(link_id,
                                                       WmediumdERRPROBLink(intfref1, intfref2,
                                                                           cls.default_auto_errprob))
                            else:
                                mappedlinks.setdefault(link_id,
                                                       WmediumdSNRLink(intfref1, intfref2, cls.default_auto_snr))

            # Create wmediumd config
            wmd_config = tempfile.NamedTemporaryFile(prefix='mn_wmd_config_', suffix='.cfg', delete=False)
            cls.wmd_config_name = wmd_config.name
            info("Name of wmediumd config: %s\n" % cls.wmd_config_name)
            configstr = 'ifaces:\n{\n\tids = ['
            intfref_id = 0
            for intfref in cls.intfrefs:
                if intfref_id != 0:
                    configstr += ', '
                grepped_mac = intfref.get_intf_mac()
                configstr += '"%s"' % grepped_mac
                mappedintfrefs[intfref.identifier()] = intfref_id
                intfref_id += 1
            configstr += '];\n};model:\n{\n\ttype = "'
            if cls.mode == WmediumdConstants.WMEDIUMD_MODE_ERRPROB:
                configstr += 'prob'
            else:
                configstr += 'snr'
            configstr += '";\n\tdefault_prob = 1.0;\n\tlinks = ('
            first_link = True
            for mappedlink in mappedlinks.itervalues():
                id1 = mappedlink.sta1intfref.identifier()
                id2 = mappedlink.sta2intfref.identifier()
                if first_link:
                    first_link = False
                else:
                    configstr += ','
                if cls.mode == WmediumdConstants.WMEDIUMD_MODE_ERRPROB:
                    configstr += '\n\t\t(%d, %d, %f)' % (
                        mappedintfrefs[id1], mappedintfrefs[id2],
                        mappedlink.errprob)
                else:
                    configstr += '\n\t\t(%d, %d, %d)' % (
                        mappedintfrefs[id1], mappedintfrefs[id2],
                        mappedlink.snr)
            configstr += '\n\t);\n};'
            wmd_config.write(configstr)
            wmd_config.close()

        # Start wmediumd using the created config
        cmdline = [cls.executable]
        if cls.mode == WmediumdConstants.WMEDIUMD_MODE_SPECPROB:
            cmdline.append("-d")
        else:
            cmdline.append("-c")
            cmdline.append(cls.wmd_config_name)
            if cls.mode == WmediumdConstants.WMEDIUMD_MODE_SNR:
                cmdline.append("-x")
                per_data_file = pkg_resources.resource_filename('wmediumd-python-connector',
                                                                'data/signal_table_ieee80211ax')
                cmdline.append(per_data_file)
        cmdline[1:1] = cls.parameters
        cls.wmd_logfile = tempfile.NamedTemporaryFile(prefix='mn_wmd_log_', suffix='.log', delete=not cls.is_managed)
        info("Name of wmediumd log: %s\n" % cls.wmd_logfile.name)
        if cls.is_managed:
            cmdline[0:0] = ["nohup"]
        cls.wmd_process = subprocess.Popen(cmdline, shell=False, stdout=cls.wmd_logfile,
                                           stderr=subprocess.STDOUT, preexec_fn=os.setpgrp)
        cls.is_connected = True

    @classmethod
    def start_managed(cls):
        """
        Start the connector in managed mode, which means disconnect and kill won't work,
        the process is kept running after the python process has finished
        """
        cls.is_managed = True
        if not cls.is_initialized:
            cls.initialize()
        cls.start()

    @classmethod
    def stop(cls):
        """
        Kill the wmediumd process if running and delete the config
        """
        if cls.is_managed:
            error("\nCannot close WmediumdStarter in managed mode")
            return
        if cls.is_connected:
            cls.kill_wmediumd()
            try:
                cls.wmd_logfile.close()
            except OSError:
                pass
            try:
                os.remove(cls.wmd_config_name)
            except OSError:
                pass
            cls.is_connected = False
        else:
            raise WmediumdException('WmediumdStarter is not connected to wmediumd')

    @classmethod
    def kill_wmediumd(cls):
        if cls.is_managed:
            return
        if not cls.is_connected:
            raise WmediumdException('WmediumdStarter is not connected to wmediumd')
        try:
            # SIGINT to allow closing resources
            cls.wmd_process.send_signal(signal.SIGINT)
            time.sleep(0.5)
            # SIGKILL in case it did not finish
            cls.wmd_process.send_signal(signal.SIGKILL)
        except OSError:
            pass


class WmediumdSNRLink(object):
    def __init__(self, sta1intfref, sta2intfref, snr=10):
        """
        Describes a link between two interfaces using the SNR

        :param sta1intfref: Instance of WmediumdIntfRef
        :param sta2intfref: Instance of WmediumdIntfRef
        :param snr: Signal Noise Ratio as int

        :type sta1intfref: WmediumdIntfRef
        :type sta2intfref: WmediumdIntfRef
        :type snr: int
        """
        self.sta1intfref = sta1intfref
        self.sta2intfref = sta2intfref
        self.snr = snr


class WmediumdERRPROBLink(object):
    def __init__(self, sta1intfref, sta2intfref, errprob=0.2):
        """
        Describes a link between two interfaces using the error probability

        :param sta1intfref: Instance of WmediumdIntfRef
        :param sta2intfref: Instance of WmediumdIntfRef
        :param errprob: The error probability in the range [0.0;1.0]

        :type sta1intfref: WmediumdIntfRef
        :type sta2intfref: WmediumdIntfRef
        :type errprob: float
        """
        self.sta1intfref = sta1intfref
        self.sta2intfref = sta2intfref
        self.errprob = errprob


class WmediumdSPECPROBLink(object):
    def __init__(self, sta1intfref, sta2intfref, errprobs):
        """
        Describes a link between two interfaces using a matrix of error probabilities

        :param sta1intfref: Instance of WmediumdIntfRef
        :param sta2intfref: Instance of WmediumdIntfRef
        :param errprobs: The error probabilities in the range [0.0;1.0], the two dimensional array has as first
            dimension the packet size index and as the second the data rate index: errprobs[size_idx][rate_idx]

        :type sta1intfref: WmediumdIntfRef
        :type sta2intfref: WmediumdIntfRef
        :type errprobs: float[][]
        """
        self.sta1intfref = sta1intfref
        self.sta2intfref = sta2intfref
        self.errprobs = errprobs


class WmediumdIntfRef:
    def __init__(self, staname, intfname, intfmac):
        """
        An unambiguous reference to an interface of a station

        :param staname: Station name
        :param intfname: Interface name
        :param intfmac: Interface MAC address

        :type staname: str
        :type intfname: str
        :type intfmac: str
        """
        self.__staname = staname
        self.__intfname = intfname
        self.__intfmac = intfmac

    def get_station_name(self):
        """
        Get the name of the station

        :rtype: str
        """
        return self.__staname

    def get_intf_name(self):
        """
        Get the interface name

        :rtype: str
        """
        return self.__intfname

    def get_intf_mac(self):
        """
        Get the MAC address of the interface

        :rtype: str
        """
        return self.__intfmac

    def identifier(self):
        """
        Identifier used in dicts

        :rtype: str
        """
        return self.get_station_name() + "." + self.get_intf_name()


class WmediumdServerConn(object):
    __mac_struct_fmt = '6s'

    __base_struct_fmt = 'B'
    __base_struct = struct.Struct('!' + __base_struct_fmt)

    __snr_update_request_fmt = __base_struct_fmt + __mac_struct_fmt + __mac_struct_fmt + 'i'
    __snr_update_response_fmt = __base_struct_fmt + __snr_update_request_fmt + 'B'
    __snr_update_request_struct = struct.Struct('!' + __snr_update_request_fmt)
    __snr_update_response_struct = struct.Struct('!' + __snr_update_response_fmt)

    __errprob_update_request_fmt = __base_struct_fmt + __mac_struct_fmt + __mac_struct_fmt + 'i'
    __errprob_update_response_fmt = __base_struct_fmt + __errprob_update_request_fmt + 'B'
    __errprob_update_request_struct = struct.Struct('!' + __errprob_update_request_fmt)
    __errprob_update_response_struct = struct.Struct('!' + __errprob_update_response_fmt)

    __specprob_update_request_fmt = __base_struct_fmt + __mac_struct_fmt + __mac_struct_fmt + '144i'
    __specprob_update_response_fmt = __base_struct_fmt + __mac_struct_fmt + __mac_struct_fmt + 'B'
    __specprob_update_request_struct = struct.Struct('!' + __specprob_update_request_fmt)
    __specprob_update_response_struct = struct.Struct('!' + __specprob_update_response_fmt)

    __station_del_by_mac_request_fmt = __base_struct_fmt + __mac_struct_fmt
    __station_del_by_mac_response_fmt = __base_struct_fmt + __station_del_by_mac_request_fmt + 'B'
    __station_del_by_mac_request_struct = struct.Struct('!' + __station_del_by_mac_request_fmt)
    __station_del_by_mac_response_struct = struct.Struct('!' + __station_del_by_mac_response_fmt)

    __station_del_by_id_request_fmt = __base_struct_fmt + 'i'
    __station_del_by_id_response_fmt = __base_struct_fmt + __station_del_by_id_request_fmt + 'B'
    __station_del_by_id_request_struct = struct.Struct('!' + __station_del_by_id_request_fmt)
    __station_del_by_id_response_struct = struct.Struct('!' + __station_del_by_id_response_fmt)

    __station_add_request_fmt = __base_struct_fmt + __mac_struct_fmt
    __station_add_response_fmt = __base_struct_fmt + __station_add_request_fmt + 'iB'
    __station_add_request_struct = struct.Struct('!' + __station_add_request_fmt)
    __station_add_response_struct = struct.Struct('!' + __station_add_response_fmt)

    sock = None
    connected = False

    @classmethod
    def connect(cls, uds_address=WmediumdConstants.SOCKET_PATH):
        # type: (str) -> None
        """
        Connect to the wmediumd server
        :param uds_address: The UNIX domain socket
        """
        if cls.connected:
            raise WmediumdException("Already connected to wmediumd server")
        cls.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        print '*** Connecting to wmediumd server %s' % uds_address
        cls.sock.connect(uds_address)
        cls.connected = True

    @classmethod
    def disconnect(cls):
        # type: () -> None
        """
        Disconnect from the wmediumd server
        """
        if not cls.connected:
            raise WmediumdException("Not yet connected to wmediumd server")
        cls.sock.close()
        cls.connected = False

    @classmethod
    def register_interface(cls, mac):
        # type: (str) -> int
        """
        Register a new interface at wmediumd
        :param mac The mac address of the interface
        :return The wmediumd station index

        :type mac: str
        :rtype int
        """
        info("%s Registering interface with mac %s\n" % (WmediumdConstants.LOG_PREFIX, mac))
        ret, sta_id = WmediumdServerConn.send_add(mac)
        if ret != WmediumdConstants.WUPDATE_SUCCESS:
            raise WmediumdException("Received error code from wmediumd: code %d" % ret)
        return sta_id

    @classmethod
    def unregister_interface(cls, mac):
        # type: (str) -> None
        """
        Unregister a station at wmediumd
        :param mac The mac address of the interface

        :type mac: str
        """
        info("%s Unregistering interface with mac %s\n" % (WmediumdConstants.LOG_PREFIX, mac))
        ret = WmediumdServerConn.send_del_by_mac(mac)
        if ret != WmediumdConstants.WUPDATE_SUCCESS:
            raise WmediumdException("Received error code from wmediumd: code %d" % ret)

    @classmethod
    def update_link_snr(cls, link):
        # type: (WmediumdSNRLink) -> None
        """
        Update the SNR of a connection at wmediumd
        :param link The link to update

        :type link: WmediumdLink
        """
        ret = WmediumdServerConn.send_snr_update(link)
        if ret != WmediumdConstants.WUPDATE_SUCCESS:
            raise WmediumdException("Received error code from wmediumd: code %d" % ret)

    @classmethod
    def update_link_errprob(cls, link):
        # type: (WmediumdERRPROBLink) -> None
        """
        Update the ERRPROB of a connection at wmediumd
        :param link The link to update

        :type link: WmediumdLink
        """
        ret = WmediumdServerConn.send_errprob_update(link)
        if ret != WmediumdConstants.WUPDATE_SUCCESS:
            raise WmediumdException("Received error code from wmediumd: code %d" % ret)

    @classmethod
    def update_link_specprob(cls, link):
        # type: (WmediumdSPECPROBLink) -> None
        """
        Update the SPECPROB of a connection at wmediumd
        :param link The link to update

        :type link: WmediumdLink
        """
        ret = WmediumdServerConn.send_specprob_update(link)
        if ret != WmediumdConstants.WUPDATE_SUCCESS:
            raise WmediumdException("Received error code from wmediumd: code %d" % ret)

    @classmethod
    def send_snr_update(cls, link):
        # type: (WmediumdSNRLink) -> int
        """
        Send an update to the wmediumd server
        :param link: The WmediumdSNRLink to update
        :return: A WUPDATE_* constant
        """
        debug("\n%s Updating SNR from interface %s to interface %s to value %d" % (
            WmediumdConstants.LOG_PREFIX, link.sta1intfref.get_intf_mac(), link.sta2intfref.get_intf_mac(), link.snr))
        cls.sock.send(cls.__create_snr_update_request(link))
        return cls.__parse_response(WmediumdConstants.WSERVER_SNR_UPDATE_RESPONSE_TYPE,
                                    cls.__snr_update_response_struct)[-1]

    @classmethod
    def send_errprob_update(cls, link):
        # type: (WmediumdERRPROBLink) -> int
        """
        Send an update to the wmediumd server
        :param link: The WmediumdERRPROBLink to update
        :return: A WUPDATE_* constant
        """
        debug("\n%s Updating ERRPROB from interface %s to interface %s to value %f" % (
            WmediumdConstants.LOG_PREFIX, link.sta1intfref.get_intf_mac(), link.sta2intfref.get_intf_mac(),
            link.errprob))
        cls.sock.send(cls.__create_errprob_update_request(link))
        return cls.__parse_response(WmediumdConstants.WSERVER_ERRPROB_UPDATE_RESPONSE_TYPE,
                                    cls.__errprob_update_response_struct)[-1]

    @classmethod
    def send_specprob_update(cls, link):
        # type: (WmediumdSPECPROBLink) -> int
        """
        Send an update to the wmediumd server
        :param link: The WmediumdSPECPROBLink to update
        :return: A WUPDATE_* constant
        """
        debug("\n%s Updating SPECPROB from interface %s to interface %s" % (
            WmediumdConstants.LOG_PREFIX, link.sta1intfref.get_intf_mac(), link.sta2intfref.get_intf_mac()))
        cls.sock.send(cls.__create_specprob_update_request(link))
        return cls.__parse_response(WmediumdConstants.WSERVER_SPECPROB_UPDATE_RESPONSE_TYPE,
                                    cls.__specprob_update_response_struct)[-1]

    @classmethod
    def send_del_by_mac(cls, mac):
        # type: (str) -> int
        """
        Send an update to the wmediumd server
        :param mac: The mac address of the interface to be deleted
        :return: A WUPDATE_* constant
        """
        cls.sock.send(cls.__create_station_del_by_mac_request(mac))
        return cls.__parse_response(WmediumdConstants.WSERVER_DEL_BY_MAC_RESPONSE_TYPE,
                                    cls.__station_del_by_mac_response_struct)[-1]

    @classmethod
    def send_del_by_id(cls, sta_id):
        # type: (int) -> int
        """
        Send an update to the wmediumd server
        :param sta_id: The wmediumd index of the station
        :return: A WUPDATE_* constant
        """
        cls.sock.send(cls.__create_station_del_by_id_request(sta_id))
        return cls.__parse_response(WmediumdConstants.WSERVER_DEL_BY_ID_RESPONSE_TYPE,
                                    cls.__station_del_by_id_response_struct)[-1]

    @classmethod
    def send_add(cls, mac):
        # type: (str) -> (int, int)
        """
        Send an update to the wmediumd server
        :param mac: The mac address of the new interface
        :return: A WUPDATE_* constant and on success at the second position the index
        """
        cls.sock.send(cls.__create_station_add_request(mac))
        resp = cls.__parse_response(WmediumdConstants.WSERVER_ADD_RESPONSE_TYPE,
                                    cls.__station_add_response_struct)
        return resp[-1], resp[-2]

    @classmethod
    def __create_snr_update_request(cls, link):
        # type: (WmediumdSNRLink) -> str
        msgtype = WmediumdConstants.WSERVER_SNR_UPDATE_REQUEST_TYPE
        mac_from = link.sta1intfref.get_intf_mac().replace(':', '').decode('hex')
        mac_to = link.sta2intfref.get_intf_mac().replace(':', '').decode('hex')
        snr = link.snr
        return cls.__snr_update_request_struct.pack(msgtype, mac_from, mac_to, snr)

    @classmethod
    def __create_errprob_update_request(cls, link):
        # type: (WmediumdERRPROBLink) -> str
        msgtype = WmediumdConstants.WSERVER_ERRPROB_UPDATE_REQUEST_TYPE
        mac_from = link.sta1intfref.get_intf_mac().replace(':', '').decode('hex')
        mac_to = link.sta2intfref.get_intf_mac().replace(':', '').decode('hex')
        errprob = cls.__conv_float_to_fixed_point(link.errprob)
        return cls.__errprob_update_request_struct.pack(msgtype, mac_from, mac_to, errprob)

    @classmethod
    def __create_specprob_update_request(cls, link):
        # type: (WmediumdSPECPROBLink) -> str
        msgtype = WmediumdConstants.WSERVER_SPECPROB_UPDATE_REQUEST_TYPE
        mac_from = link.sta1intfref.get_intf_mac().replace(':', '').decode('hex')
        mac_to = link.sta2intfref.get_intf_mac().replace(':', '').decode('hex')
        fixed_points = [None] * 144
        for size_idx in range(0, 12):
            for rate_idx in range(0, 12):
                fixed_points[size_idx * 12 + rate_idx] = cls.__conv_float_to_fixed_point(
                    link.errprobs[size_idx][rate_idx])
        return cls.__specprob_update_request_struct.pack(msgtype, mac_from, mac_to, *fixed_points)

    @classmethod
    def __create_station_del_by_mac_request(cls, mac):
        # type: (str) -> str
        msgtype = WmediumdConstants.WSERVER_DEL_BY_MAC_REQUEST_TYPE
        macparsed = mac.replace(':', '').decode('hex')
        return cls.__station_del_by_mac_request_struct.pack(msgtype, macparsed)

    @classmethod
    def __create_station_del_by_id_request(cls, sta_id):
        # type: (int) -> str
        msgtype = WmediumdConstants.WSERVER_DEL_BY_ID_REQUEST_TYPE
        return cls.__station_del_by_id_request_struct.pack(msgtype, sta_id)

    @classmethod
    def __create_station_add_request(cls, mac):
        # type: (str) -> str
        msgtype = WmediumdConstants.WSERVER_ADD_REQUEST_TYPE
        macparsed = mac.replace(':', '').decode('hex')
        return cls.__station_add_request_struct.pack(msgtype, macparsed)

    @classmethod
    def __parse_response(cls, expected_type, resp_struct):
        # type: (int, struct.Struct) -> tuple
        recvd_data = cls.sock.recv(resp_struct.size)
        recvd_type = cls.__base_struct.unpack(recvd_data[0])[0]
        if recvd_type != expected_type:
            raise WmediumdException(
                "Received response of unknown type %d, expected %d" % (recvd_type, expected_type))
        return resp_struct.unpack(recvd_data)

    @classmethod
    def __conv_float_to_fixed_point(cls, d):
        shift_amount = 31
        one_shifted = ctypes.c_int32(1 << 31).value
        beforecomma = int(d)
        aftercomma = int(((d - beforecomma) * one_shifted))
        return ctypes.c_int32(beforecomma << shift_amount).value + aftercomma
