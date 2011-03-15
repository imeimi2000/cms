#!/usr/bin/python

"""This file defines classes to handle asynchronous RPC communication
usinc asynchat and JSON encoding.

"""

import socket
import time
import sys
import os

import asyncore
import asynchat
import simplejson
import datetime
import codecs

from Config import get_service_address
from Utils import Address, ServiceCoord, random_string


def encode_json(obj):
    """Encode a dictionary as a JSON string; on failure, returns None.

    obj (object): the object to encode
    return (string): an encoded string
    """
    try:
        return simplejson.dumps(obj)
    except:
        logger.error("Can't encode JSON: %s" % repr(obj))
        raise ValueError


def decode_json(string):
    """Decode a JSON string to a dictionary; on failure, raises an
    exception.

    string (string): the Unicode string to decode.
    return (object): the decoded object.
    """
    try:
        string = string.decode("utf8")
        return simplejson.loads(string)
    except simplejson.JSONDecodeError:
        logger.error("Can't decode JSON: %s" % string)
        raise ValueError


def rpc_callback(func):
    """Tentative decorator for a RPC callback function. Up to now it
    does not do a lot, I hope to be able to manage errors in a
    Pythonic way.

    """

    def newfunc(self, *args, **kwargs):
        """Mangle __error and give back error when appropriate.

        """
        if "__error" in kwargs:
            if kwargs["__error"] != None:
                kwargs["error"] = kwargs["__error"]
# We want to be able to manage the exception, so no raise.
#                raise Exception(kwargs["__error"])
            del kwargs["__error"]
        return func(self, *args, **kwargs)

    return newfunc


def rpc_method(func):
    """Decorator for a method that other services are allowed to
    call. Does not do a lot, just defines the right method's
    attribute.

    """
    func.rpc_callable = True
    return func


class AuthorizationError(Exception):
    pass


class RPCRequest:
    """Class to keep the state of an RPC request, while we were
    waiting for the response. There is also a class variable that
    stores all the pending RPC requests.

    """
    pending_requests = {}

    def __init__(self, message, bind_obj, callback, plus):
        """Create the istance of a RPC query.

        message (object): the message to send.
        bind_obj (object): the context for the callback.
        callback (function): the function to call on completion.
        plus (object): additional argument for callback.

        """
        logger.debug("RPCRequest.__init__")
        self.message = message
        self.bind_obj = bind_obj
        self.callback = callback
        self.plus = plus

    def pre_execute(self):
        """Store in the class the RPC request before sending it, in
        order to couple later the response.

        return (object): the object to send.
        """
        logger.debug("RPCRequest.pre_execute")
        self.message["__id"] = random_string(16)
        RPCRequest.pending_requests[self.message["__id"]] = self

        return self.message

    def complete(self, response):
        """To be called when the response arrive. It deletes the
        stored state and execute the callback.

        response (object): The response, already decoded from JSON.
        """
        logger.debug("RPCRequest.complete")
        del RPCRequest.pending_requests[response["__id"]]
        if self.callback != None:
            if self.plus == None:
                self.callback(self.bind_obj,
                              response["__data"],
                              __error=response["__error"])
            else:
                self.callback(self.bind_obj,
                              response["__data"],
                              self.plus,
                              __error=response["__error"])


class Service:
    """Interface to be subclassed of an RPC service using JSON
    encoding. It is designed for a service that accept requests and
    also query other services, but it can be used for services that
    need only one of the two behaviours.

    """
    def __init__(self, shard=0):
        logger.debug("Service.__init__")
        self.shard = shard
        self._timeouts = {}
        self._seconds = 0
        self._connections = set([])
        self.remote_services = {}

        self._my_coord = ServiceCoord(self.__class__.__name__, self.shard)
        self.add_timeout(self._reconnect, None, 10, immediately=True)
        try:
            address = get_service_address(self._my_coord)
        except KeyError:
            address = None
        if address != None:
            self.server = ListeningSocket(self, address)

    def connect_to(self, service):
        """Ask the service to connect to another service. A channel is
        established and connected. The connection will be reopened if
        closed.

        service (ServiceCoord): the service to connect to.
        return (RemoteService): the connected RemoteService istance.
        """
        self._connections.add(service)
        self.remote_services[service] = RemoteService(self, service)
        try:
            self.remote_services[service].connect_remote_service()
        except:
            pass
        return self.remote_services[service]

    def add_timeout(self, func, plus, seconds, immediately=False):
        """Registers a function to be called every tot seconds.

        func (function): the function to call.
        plus (object): additional data to pass to the function.
        seconds (float): the function will be called every seconds
                         seconds.
        immediately (bool): if True, func will be called also at the
                            beginning.

        """
        last = time.time()
        if immediately:
            last -= seconds
        self._timeouts[func] = [plus, seconds, last]

    def run(self):
        """Starts the main loop of the service.

        """
        logger.debug("Service.run")
        while True:
            self._step()

    def _step(self):
        """One step of the main loop.

        """
        # Let's not spam the logs...
        # logger.debug("Service._step")
        asyncore.loop(0.02, True, None, 1)
        self._trigger()

    def _reconnect(self):
        """Reconnect to all remote services that have been disconnected.

        """
        logger.debug("Service._reconnect")
        for service in self._connections:
            remote_service = self.remote_services[service]
            if not remote_service.connected:
                try:
                    remote_service.connect_remote_service()
                except:
                    pass
        return True

    def _trigger(self):
        """Call the timeouts that have expired.

        """
        current = time.time()
        for func in self._timeouts.keys():
            plus, seconds, timestamp = self._timeouts[func]
            if current - timestamp > seconds:
                self._timeouts[func][2] = current
                if plus == None:
                    ret = func()
                else:
                    ret = func(plus)
                if not ret:
                    del self._timeouts[func]

    @rpc_method
    def echo(self, string):
        """Simple RPC method.

        string (string): the string to be echoed.
        return (string): string, again.

        """
        logger.debug("Service.echo")
        return string

    def handle_rpc_response(self, message):
        """To be called when the channel finishes to collect a message
        that is a response. It ask the RPCRequest do complete the
        conversation.

        message (object): the decoded message.
        """
        logger.debug("Service.handle_rpc_response")
        if "__id" not in message:
            return
        ident = message["__id"]
        if ident in RPCRequest.pending_requests:
            rpc = RPCRequest.pending_requests[ident]
            rpc.complete(message)
        else:
            logger.error("No pending request with id %s found." % ident)

    def handle_message(self, message):
        """To be called when the channel finishes to collect a message
        that is a RPC request. It calls the requested method.

        message (object): the decoded message.
        return (bool, object): (False, None) if it was a response,
                               (True, result) if it was a request.
        """
        logger.debug("Service.handle_message")

        method_name = message["__method"]
        try:
            method = getattr(self, method_name)
        except:
            raise KeyError("Service has no method " + method_name)

        if not hasattr(method, "rpc_callable"):
            raise AuthorizationError("Method %s not callable from RPC" %
                                     method)

        if "__data" not in message:
            raise ValueError("No data present.")

        result = method(**message["__data"])

        return result


class RemoteService(asynchat.async_chat):
    """This class mimick the local presence of a remote service. A
    local service can define many RemoteService object and call
    methods of those services almost as if they were local. Almost
    because being asynchronous, the responses of the requests have to
    be collected using callback functions.

    """

    def __init__(self, service, remote_service_coord=None, address=None):
        """Create a communication channel to a remote service.

        service (Service): the local service.
        remote_service_coord (ServiceCoord): the description of the
                                             remote service to connect
                                             to.
        address (Address): alternatively, the address to connect to
                           (used when accepting a connection).

        """
        # Can't log using logger here, since it is not yet defined.
        # logger.debug("RemoteService.__init__")
        if address == None and remote_service_coord == None:
            raise
        self.service = service
        if address == None:
            self.remote_service_coord = remote_service_coord
            self.address = get_service_address(remote_service_coord)
        else:
            self.remote_service_coord = ""
            self.address = address
        self.connected = False
        self.data = []

    def _initialize_channel(self, sock):
        """When we have a socket, we configure the channel using this
        socket.

        sock (socket): the socket to use as a communication channel.
        """
        logger.debug("RemoteService._initialize_channel")
        asynchat.async_chat.__init__(self, sock)
        self.set_terminator("\r\n")

    def collect_incoming_data(self, data):
        """Function called when something arrived through the socket.

        data (string): arrived data.
        """
        logger.debug("RemoteService.collect_incoming_data")
        if self.service == None:
            return
        self.data.append(data)

    def found_terminator(self):
        """Function called when a terminator is detected in the
        stream. It clear the cache and decode the data. Then it ask
        the local service to act and in case the service wants to
        respond, it sends back the response.

        """
        logger.debug("RemoteService.found_terminator")
        if self.service == None:
            return
        data = "".join(self.data)
        self.data = []

        try:
            message = decode_json(data)
        except:
            return

        response = {"__data": None,
                    "__error": None}
        if "__id" in message:
            response["__id"] = message["__id"]
        if "__method" in message:
            try:
                response["__data"] = self.service.handle_message(message)
            except Exception, exception:
                response["__error"] = "%s: %s" % (
                    exception.__class__.__name__,
                    " ".join([str(x) for x in exception.args]))
            try:
                json_string = encode_json(response)
            except ValueError:
                logger.error("Cannot send response because of " +
                          "JSON encoding error.")
                return
            self._push_right(json_string)
        else:
            self.service.handle_rpc_response(message)

    def execute_rpc(self, method, data,
                    callback=None, plus=None, bind_obj=None):
        """Method to send an RPC request to the remote service.

        method (string): the name of the method to call.
        data (object): the object to pass to the remote method.
        callback (function): method to call with the RPC response.
        plus (object): additional object to be passed to the callback.
        bind_obj (object): context for the callback (None means the
                           local service).

        """
        logger.debug("RemoteService.execute_rpc")
        if not self.connected:
            self.connect_remote_service()
            if not self.connected:
                return False
        if bind_obj == None:
            bind_obj = self.service
        message = {}
        message["__method"] = method
        message["__data"] = data
        request = RPCRequest(message, bind_obj, callback, plus)
        try:
            json_string = encode_json(request.pre_execute())
        except ValueError:
            logger.error("Cannot send request because of " +
                      "JSON encoding error.")
            request.complete(None)
            return
        self._push_right(json_string)
        return True

    def __getattr__(self, method):
        """Syntactic sugar to call a remote method without using
        execute_rpc. If the local service ask for something that is
        not present, we assume that it is a remote RPC method.

        method (string): the method to call.

        """
        logger.debug("RemoteService.__getattr__(%s)" % method)

        def remote_method(callback=None,
                          plus=None,
                          bind_obj=None,
                          **data):
            """Call execute_rpc with the given method name.

            """
            return self.execute_rpc(method, data,
                                    callback, plus, bind_obj)
        return remote_method

    def _push_right(self, data):
        """Send a request or a response with the right terminator in
        the end.

        data (string): the data to send.

        """
        logger.debug("RemoteService._push_right")
        to_push = "".join(data) + "\r\n"
        self.push(to_push)

    def handle_error(self):
        """Handle a generic error in the communication.

        """
        logger.debug("RemoteService.handle_error")
        self.handle_close()
        raise

    def handle_close(self):
        """Handle the case when the connection fall.

        """
        logger.debug("RemoteService.handle_close")
        self.close()
        self.connected = False

    def connect_remote_service(self):
        """Try to connect to the remote service.

        """
        logger.debug("RemoteService.connect_remote_service")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(self.address)
        except:
            return
        else:
            self.connected = True
            self._initialize_channel(sock)


class ListeningSocket(asyncore.dispatcher):
    """This class starts a listening socket. It is needed by a Service
    that wants to be able to receive RPC requests.

    """

    def __init__(self, service, address):
        """This creates a listening socket for the service at the
        specified address.

        service (Service): this socket listens for this service.
        address (Address): the address to listen at.

        """
        logger.debug("ListeningSocket.__init__")
        asyncore.dispatcher.__init__(self)
        self._service = service
        self._address = address

        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.bind(("", self._address.port))
        self.listen(5)

    def handle_accept(self):
        """Handle a connection request. It creates a RemoteService to
        manage the connection.

        """
        logger.debug("ListeningSocket.handle_accept")
        try:
            connection, address = self.accept()
        except socket.error:
            logger.error("Error: %s %s" % (sys.exc_info()[:2]))
            return
        try:
            ipaddr, port = socket.getnameinfo(address, socket.NI_NOFQDN)
            address = Address(ipaddr, int(port))
        except:
            logger.error("Error: %s %s" % (sys.exc_info()[:2]))
            return
        remote_service = RemoteService(self._service,
                                       address=address)
        remote_service._initialize_channel(connection)
        self.connected = True

    def handle_close(self):
        """Handle when the connection falls.

        """
        logger.debug("ListeningSocket.handle_close")


class Logger:
    """Utility class to connect to the remote log service and to
    store/display locally and remotely log messages.

    """


    def __init__(self):
        Logger.CRITICAL = "CRITICAL"
        Logger.ERROR    = "ERROR   "
        Logger.INFO     = "INFO    "
        Logger.DEBUG    = "DEBUG   "

        Logger.TO_STORE = [
            Logger.CRITICAL,
            Logger.ERROR,
            Logger.INFO,
            Logger.DEBUG,
            ]
        Logger.TO_DISPLAY = [
            Logger.CRITICAL,
            Logger.ERROR,
            Logger.INFO,
            ]
        Logger.TO_SEND = [
            Logger.CRITICAL,
            Logger.ERROR,
            Logger.INFO,
            ]

        self._log_service = RemoteService(None,
                                          ServiceCoord("LogService", 0))

    def initialize(self, service):
        """To be set by the service we are currently running.

        service (ServiceCoord): the service that we are running

        """
        self._my_coord = service
        self._log_file = codecs.open(\
            os.path.join("logs","%d-%s-%d.local-log" %
                         (time.time(), service.name, service.shard)),
            "w", "utf-8")


    def log(self, msg, operation="", severity=None, timestamp=None):
        """Record locally a log message and tries to send it to the
        log service.

        msg (string): the message to log
        operation (string): a high-level description of the long-term
                            operation that is going on in the service
        severity (string): a constant defined in Logger
        timestamp (float): seconds from epoch

        """
        if severity == None:
            severity = Logger.INFO
        if timestamp == None:
            timestamp = time.time()

        log = self.format_log(msg, operation, severity, timestamp)

        if severity in Logger.TO_DISPLAY:
            print log
        if severity in Logger.TO_STORE:
            print >> self._log_file, log
        if severity in Logger.TO_SEND:
            self._log_service.Log(msg=log)

    def debug(self, msg, operation="", timestamp=None):
        """Syntactic sugar.

        """
        return self.log(msg, operation, Logger.DEBUG, timestamp)

    def info(self, msg, operation="", timestamp=None):
        """Syntactic sugar.

        """
        return self.log(msg, operation, Logger.INFO, timestamp)

    def error(self, msg, operation="", timestamp=None):
        """Syntactic sugar.

        """
        return self.log(msg, operation, Logger.ERROR, timestamp)

    def critical(self, msg, operation="", timestamp=None):
        """Syntactic sugar.

        """
        return self.log(msg, operation, Logger.CRITICAL, timestamp)

    def format_log(self, msg, operation, severity, timestamp):
        """Format a log message in a common way (for local and remote
        logging).

        msg (string): the message to log
        operation (string): a high-level description of the long-term
                            operation that is going on in the service
        severity (string): a constant defined in Logger
        timestamp (float): seconds from epoch
        returns (string): the formatted log

        """
        d = datetime.datetime.fromtimestamp(timestamp)
        service_full = repr(self._my_coord)
        if operation != "":
            service_full += "/%s" % (operation)
        return "%s - %s [%s] %s" % ('{0:%Y/%m/%d %H:%M:%S}'.format(d),
                                    severity, service_full, msg)


logger = Logger()
