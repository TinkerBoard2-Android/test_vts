#
#   Copyright 2016 - The Android Open Source Project
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from builtins import str
from builtins import open

import logging
import os
import time
import traceback
import threading
import socket

from vts.runners.host import keys
from vts.runners.host import logger as vts_logger
from vts.runners.host import signals
from vts.runners.host import utils
from vts.utils.python.controllers import adb
from vts.utils.python.controllers import event_dispatcher
from vts.utils.python.controllers import fastboot
from vts.utils.python.controllers import sl4a_client
from vts.runners.host.tcp_client import vts_tcp_client
from vts.utils.python.mirror import hal_mirror
from vts.utils.python.mirror import shell_mirror
from vts.utils.python.mirror import lib_mirror
from vts.runners.host import errors
import subprocess

VTS_CONTROLLER_CONFIG_NAME = "AndroidDevice"
VTS_CONTROLLER_REFERENCE_NAME = "android_devices"

ANDROID_DEVICE_PICK_ALL_TOKEN = "*"
# Key name for adb logcat extra params in config file.
ANDROID_DEVICE_ADB_LOGCAT_PARAM_KEY = "adb_logcat_param"
ANDROID_DEVICE_EMPTY_CONFIG_MSG = "Configuration is empty, abort!"
ANDROID_DEVICE_NOT_LIST_CONFIG_MSG = "Configuration should be a list, abort!"

ANDROID_PRODUCT_TYPE_UNKNOWN = "unknown"

# Target-side directory where the VTS binaries are uploaded
DEFAULT_AGENT_BASE_DIR = "/data/local/tmp"
# Time for which the current is put on sleep when the client is unable to
# make a connection.
THREAD_SLEEP_TIME = 1
# Max number of attempts that the client can make to connect to the agent
MAX_AGENT_CONNECT_RETRIES = 10


class AndroidDeviceError(signals.ControllerError):
    pass


def create(configs, start_services=True):
    """Creates AndroidDevice controller objects.

    Args:
        configs: A list of dicts, each representing a configuration for an
                 Android device.
        start_services: boolean, controls whether services will be started.

    Returns:
        A list of AndroidDevice objects.
    """
    if not configs:
        raise AndroidDeviceError(ANDROID_DEVICE_EMPTY_CONFIG_MSG)
    elif configs == ANDROID_DEVICE_PICK_ALL_TOKEN:
        ads = get_all_instances()
    elif not isinstance(configs, list):
        raise AndroidDeviceError(ANDROID_DEVICE_NOT_LIST_CONFIG_MSG)
    elif isinstance(configs[0], str):
        # Configs is a list of serials.
        ads = get_instances(configs)
    else:
        # Configs is a list of dicts.
        ads = get_instances_with_configs(configs)
    connected_ads = list_adb_devices()
    for ad in ads:
        if ad.serial not in connected_ads:
            raise DoesNotExistError(("Android device %s is specified in config"
                                     " but is not attached.") % ad.serial)
    if start_services:
        _startServicesOnAds(ads)
    return ads


def destroy(ads):
    """Cleans up AndroidDevice objects.

    Args:
        ads: A list of AndroidDevice objects.
    """
    for ad in ads:
        try:
            ad.cleanUp()
        except:
            ad.log.exception("Failed to clean up properly.")


def _startServicesOnAds(ads):
    """Starts long running services on multiple AndroidDevice objects.

    If any one AndroidDevice object fails to start services, cleans up all
    existing AndroidDevice objects and their services.

    Args:
        ads: A list of AndroidDevice objects whose services to start.
    """
    running_ads = []
    for ad in ads:
        running_ads.append(ad)
        try:
            ad.startServices()
        except:
            ad.log.exception("Failed to start some services, abort!")
            destroy(running_ads)
            raise


def _parse_device_list(device_list_str, key):
    """Parses a byte string representing a list of devices. The string is
    generated by calling either adb or fastboot.

    Args:
        device_list_str: Output of adb or fastboot.
        key: The token that signifies a device in device_list_str.

    Returns:
        A list of android device serial numbers.
    """
    clean_lines = str(device_list_str, 'utf-8').strip().split('\n')
    results = []
    for line in clean_lines:
        tokens = line.strip().split('\t')
        if len(tokens) == 2 and tokens[1] == key:
            results.append(tokens[0])
    return results


def list_adb_devices():
    """List all target devices connected to the host and detected by adb.

    Returns:
        A list of android device serials. Empty if there's none.
    """
    out = adb.AdbProxy().devices()
    return _parse_device_list(out, "device")


def list_fastboot_devices():
    """List all android devices connected to the computer that are in in
    fastboot mode. These are detected by fastboot.

    Returns:
        A list of android device serials. Empty if there's none.
    """
    out = fastboot.FastbootProxy().devices()
    return _parse_device_list(out, "fastboot")


def get_instances(serials):
    """Create AndroidDevice instances from a list of serials.

    Args:
        serials: A list of android device serials.

    Returns:
        A list of AndroidDevice objects.
    """
    results = []
    for s in serials:
        results.append(AndroidDevice(s))
    return results


def get_instances_with_configs(configs):
    """Create AndroidDevice instances from a list of json configs.

    Each config should have the required key-value pair "serial".

    Args:
        configs: A list of dicts each representing the configuration of one
            android device.

    Returns:
        A list of AndroidDevice objects.
    """
    results = []
    for c in configs:
        try:
            serial = c.pop(keys.ConfigKeys.IKEY_SERIAL)
        except KeyError:
            raise AndroidDeviceError(('Required value %s is missing in '
                                      'AndroidDevice config %s.') %
                                     (keys.ConfigKeys.IKEY_SERIAL, c))
        try:
            product_type = c.pop(keys.ConfigKeys.IKEY_PRODUCT_TYPE)
        except KeyError:
            logging.error('Required value %s is missing in '
                          'AndroidDevice config %s.',
                          keys.ConfigKeys.IKEY_PRODUCT_TYPE, c)
            product_type = ANDROID_PRODUCT_TYPE_UNKNOWN

        ad = AndroidDevice(serial, product_type)
        ad.loadConfig(c)
        results.append(ad)
    return results


def get_all_instances(include_fastboot=False):
    """Create AndroidDevice instances for all attached android devices.

    Args:
        include_fastboot: Whether to include devices in bootloader mode or not.

    Returns:
        A list of AndroidDevice objects each representing an android device
        attached to the computer.
    """
    if include_fastboot:
        serial_list = list_adb_devices() + list_fastboot_devices()
        return get_instances(serial_list)
    return get_instances(list_adb_devices())


def filter_devices(ads, func):
    """Finds the AndroidDevice instances from a list that match certain
    conditions.

    Args:
        ads: A list of AndroidDevice instances.
        func: A function that takes an AndroidDevice object and returns True
            if the device satisfies the filter condition.

    Returns:
        A list of AndroidDevice instances that satisfy the filter condition.
    """
    results = []
    for ad in ads:
        if func(ad):
            results.append(ad)
    return results


def get_device(ads, **kwargs):
    """Finds a unique AndroidDevice instance from a list that has specific
    attributes of certain values.

    Example:
        get_device(android_devices, label="foo", phone_number="1234567890")
        get_device(android_devices, model="angler")

    Args:
        ads: A list of AndroidDevice instances.
        kwargs: keyword arguments used to filter AndroidDevice instances.

    Returns:
        The target AndroidDevice instance.

    Raises:
        AndroidDeviceError is raised if none or more than one device is
        matched.
    """

    def _get_device_filter(ad):
        for k, v in kwargs.items():
            if not hasattr(ad, k):
                return False
            elif getattr(ad, k) != v:
                return False
        return True

    filtered = filter_devices(ads, _get_device_filter)
    if not filtered:
        raise AndroidDeviceError(("Could not find a target device that matches"
                                  " condition: %s.") % kwargs)
    elif len(filtered) == 1:
        return filtered[0]
    else:
        serials = [ad.serial for ad in filtered]
        raise AndroidDeviceError("More than one device matched: %s" % serials)


def takeBugReports(ads, test_name, begin_time):
    """Takes bug reports on a list of android devices.

    If you want to take a bug report, call this function with a list of
    android_device objects in on_fail. But reports will be taken on all the
    devices in the list concurrently. Bug report takes a relative long
    time to take, so use this cautiously.

    Args:
        ads: A list of AndroidDevice instances.
        test_name: Name of the test case that triggered this bug report.
        begin_time: Logline format timestamp taken when the test started.
    """
    begin_time = vts_logger.normalizeLogLineTimestamp(begin_time)

    def take_br(test_name, begin_time, ad):
        ad.takeBugReport(test_name, begin_time)

    args = [(test_name, begin_time, ad) for ad in ads]
    utils.concurrent_exec(take_br, args)


class AndroidDevice(object):
    """Class representing an android device.

    Each object of this class represents one Android device. The object holds
    handles to adb, fastboot, and various RPC clients.

    Attributes:
        serial: A string that's the serial number of the Android device.
        device_command_port: int, the port number used on the Android device
                for adb port forwarding (for command-response sessions).
        device_callback_port: int, the port number used on the Android device
                for adb port reverse forwarding (for callback sessions).
        log: A logger project with a device-specific prefix for each line -
             [AndroidDevice|<serial>]
        log_path: A string that is the path where all logs collected on this
                  android device should be stored.
        adb_logcat_process: A process that collects the adb logcat.
        adb_logcat_file_path: A string that's the full path to the adb logcat
                              file collected, if any.
        vts_agent_process: A process that runs the HAL agent.
        adb: An AdbProxy object used for interacting with the device via adb.
        fastboot: A FastbootProxy object used for interacting with the device
                  via fastboot.
        host_command_port: the host-side port for runner to agent sessions
                           (to send commands and receive responses).
        host_callback_port: the host-side port for agent to runner sessions
                            (to get callbacks from agent).
        hal: HalMirror, in charge of all communications with the HAL layer.
        lib: LibMirror, in charge of all communications with static and shared
             native libs.
        shell: ShellMirror, in charge of all communications with shell.
        _product_type: A string, the device product type (e.g., bullhead) if
                       known, ANDROID_PRODUCT_TYPE_UNKNOWN otherwise.
    """

    def __init__(self,
                 serial="",
                 product_type=ANDROID_PRODUCT_TYPE_UNKNOWN,
                 device_callback_port=5010):
        self.serial = serial
        self._product_type = product_type
        self.device_command_port = None
        self.device_callback_port = device_callback_port
        self.log = AndroidDeviceLoggerAdapter(logging.getLogger(),
                                              {"serial": self.serial})
        base_log_path = getattr(logging, "log_path", "/tmp/logs/")
        self.log_path = os.path.join(base_log_path, "AndroidDevice%s" % serial)
        self.adb_logcat_process = None
        self.adb_logcat_file_path = None
        self.vts_agent_process = None
        self.adb = adb.AdbProxy(serial)
        self.fastboot = fastboot.FastbootProxy(serial)
        if not self.isBootloaderMode:
            self.rootAdb()
        self.host_command_port = None
        self.host_callback_port = adb.get_available_host_port()
        self.adb.reverse_tcp_forward(self.device_callback_port,
                                     self.host_callback_port)
        self.hal = None
        self.lib = None
        self.shell = None
        self.sl4a_host_port = None
        # TODO: figure out a good way to detect which port is available
        # on the target side, instead of hard coding a port number.
        self.sl4a_target_port = 8082

    def __del__(self):
        self.cleanUp()

    def cleanUp(self):
        """Cleans up the AndroidDevice object and releases any resources it
        claimed.
        """
        self.stopServices()
        if self.host_command_port:
            self.adb.forward("--remove tcp:%s" % self.host_command_port)
            self.host_command_port = None
        if self.sl4a_host_port:
            self.adb.forward("--remove tcp:%s" % self.sl4a_host_port)
            self.sl4a_host_port = None

    @property
    def isBootloaderMode(self):
        """True if the device is in bootloader mode."""
        return self.serial in list_fastboot_devices()

    @property
    def isAdbRoot(self):
        """True if adb is running as root for this device."""
        id_str = self.adb.shell("id -u").decode("utf-8")
        return "root" in id_str

    @property
    def verityEnabled(self):
        """True if verity is enabled for this device."""
        try:
            verified = self.getProp("partition.system.verified")
            if not verified:
                return False
        except adb.AdbError:
            # If verity is disabled, there is no property 'partition.system.verified'
            return False
        return True

    @property
    def model(self):
        """The Android code name for the device."""
        # If device is in bootloader mode, get mode name from fastboot.
        if self.isBootloaderMode:
            out = self.fastboot.getvar("product").strip()
            # "out" is never empty because of the "total time" message fastboot
            # writes to stderr.
            lines = out.decode("utf-8").split('\n', 1)
            if lines:
                tokens = lines[0].split(' ')
                if len(tokens) > 1:
                    return tokens[1].lower()
            return None
        model = self.getProp("ro.build.product").lower()
        if model == "sprout":
            return model
        else:
            model = self.getProp("ro.product.name").lower()
            return model

    @property
    def cpu_abi(self):
        """CPU ABI (Application Binary Interface) of the device."""
        out = self.getProp("ro.product.cpu.abi")
        if not out:
            return "unknown"

        cpu_abi = out.lower()
        return cpu_abi

    @property
    def is64Bit(self):
        """True if device is 64 bit."""
        out = self.adb.shell('uname -m')
        return "64" in out

    @property
    def total_memory(self):
        """Total memory on device.

        Returns:
            long, total memory in bytes. -1 if cannot get memory information.
        """
        total_memory_command = 'cat /proc/meminfo | grep MemTotal'
        out = self.adb.shell(total_memory_command)
        value_unit = out.split(':')[-1].strip().split(' ')

        if len(value_unit) != 2:
            logging.error('Cannot get memory information. %s', out)
            return -1

        value, unit = value_unit

        try:
            value = int(value)
        except ValueError:
            logging.error('Unrecognized total memory value: %s', value_unit)
            return -1

        unit = unit.lower()
        if unit == 'kb':
            value *= 1024
        elif unit == 'mb':
            value *= 1024 * 1024
        elif unit == 'b':
            pass
        else:
            logging.error('Unrecognized total memory unit: %s', value_unit)
            return -1

        return value

    @property
    def libPaths(self):
        """List of strings representing the paths to the native library directories."""
        paths_32 = ["/system/lib", "/vendor/lib"]
        if self.is64Bit:
            paths_64 = ["/system/lib64", "/vendor/lib64"]
            paths_64.extend(paths_32)
            return paths_64
        return paths_32

    @property
    def isAdbLogcatOn(self):
        """Whether there is an ongoing adb logcat collection.
        """
        if self.adb_logcat_process:
            return True
        return False

    def loadConfig(self, config):
        """Add attributes to the AndroidDevice object based on json config.

        Args:
            config: A dictionary representing the configs.

        Raises:
            AndroidDeviceError is raised if the config is trying to overwrite
            an existing attribute.
        """
        for k, v in config.items():
            if hasattr(self, k):
                raise AndroidDeviceError(
                    "Attempting to set existing attribute %s on %s" %
                    (k, self.serial))
            setattr(self, k, v)

    def rootAdb(self):
        """Changes adb to root mode for this device."""
        if not self.isAdbRoot:
            try:
                self.adb.root()
                self.adb.wait_for_device()
                self.adb.remount()
                self.adb.wait_for_device()
            except adb.AdbError as e:
                # adb wait-for-device is not always possible in the lab
                # continue with an assumption it's done by the harness.
                logging.exception(e)

    def startAdbLogcat(self):
        """Starts a standing adb logcat collection in separate subprocesses and
        save the logcat in a file.
        """
        if self.isAdbLogcatOn:
            raise AndroidDeviceError(("Android device %s already has an adb "
                                      "logcat thread going on. Cannot start "
                                      "another one.") % self.serial)
        f_name = "adblog_%s_%s.txt" % (self.model, self.serial)
        utils.create_dir(self.log_path)
        logcat_file_path = os.path.join(self.log_path, f_name)
        try:
            extra_params = self.adb_logcat_param
        except AttributeError:
            extra_params = "-b all"
        cmd = "adb -s %s logcat -v threadtime %s >> %s" % (self.serial,
                                                           extra_params,
                                                           logcat_file_path)
        self.adb_logcat_process = utils.start_standing_subprocess(cmd)
        self.adb_logcat_file_path = logcat_file_path

    def stopAdbLogcat(self):
        """Stops the adb logcat collection subprocess.
        """
        if not self.isAdbLogcatOn:
            raise AndroidDeviceError(
                "Android device %s does not have an ongoing adb logcat collection."
                % self.serial)
        try:
            utils.stop_standing_subprocess(self.adb_logcat_process)
        except utils.VTSUtilsError as e:
            logging.error("Cannot stop adb logcat. %s", e)
        self.adb_logcat_process = None

    def takeBugReport(self, test_name, begin_time):
        """Takes a bug report on the device and stores it in a file.

        Args:
            test_name: Name of the test case that triggered this bug report.
            begin_time: Logline format timestamp taken when the test started.
        """
        br_path = os.path.join(self.log_path, "BugReports")
        utils.create_dir(br_path)
        base_name = ",%s,%s.txt" % (begin_time, self.serial)
        test_name_len = utils.MAX_FILENAME_LEN - len(base_name)
        out_name = test_name[:test_name_len] + base_name
        full_out_path = os.path.join(br_path, out_name.replace(' ', '\ '))
        self.log.info("Taking bugreport for %s on %s", test_name, self.serial)
        self.adb.bugreport(" > %s" % full_out_path)
        self.log.info("Bugreport for %s taken at %s", test_name, full_out_path)

    @utils.timeout(15 * 60)
    def waitForBootCompletion(self):
        """Waits for Android framework to broadcast ACTION_BOOT_COMPLETED.

        This function times out after 15 minutes.
        """
        try:
            self.adb.wait_for_device()
        except adb.AdbError as e:
            # adb wait-for-device is not always possible in the lab
            logging.exception(e)
        while not self.hasBooted():
            time.sleep(5)

    def hasBooted(self):
        """Checks whether the device has booted.

        Returns:
            True if booted, False otherwise.
        """
        try:
            completed = self.getProp("sys.boot_completed")
            if completed == '1':
                return True
        except adb.AdbError:
            # adb shell calls may fail during certain period of booting
            # process, which is normal. Ignoring these errors.
            return False

    def start(self):
        """Starts Android runtime and waits for ACTION_BOOT_COMPLETED."""
        logging.info("starting Android Runtime")
        self.adb.shell("start")
        self.waitForBootCompletion()
        logging.info("Android Runtime started")

    def stop(self):
        """Stops Android runtime."""
        logging.info("stopping Android Runtime")
        self.adb.shell("stop")
        self.setProp("sys.boot_completed", 0)
        logging.info("Android Runtime stopped")

    def setProp(self, name, value):
        """Calls setprop shell command.

        Args:
            name: string, the name of a system property to set
            value: any type, value will be converted to string. Quotes in value
                   is not supported at this time; if value contains a quote,
                   this method will log an error and return.

        Raises:
            AdbError, if name contains invalid character
        """
        if name is None or value is None:
            logging.error("name or value of system property "
                          "should not be None. No property is set.")
            return

        value = str(value)

        if "'" in value or "\"" in value:
            logging.error("Quotes in value of system property "
                          "is not yet supported. No property is set.")
            return

        self.adb.shell("setprop %s \"%s\"" % (name, value))

    def getProp(self, name):
        """Calls getprop shell command.

        Args:
            name: string, the name of a system property to get

        Returns:
            string, value of the property. If name does not exist; an empty
            string will be returned. decode("utf-8") and strip() will be called
            on the output before returning; None will be returned if input
            name is None

        Raises:
            AdbError, if name contains invalid character
        """
        if name is None:
            logging.error("name of system property should not be None.")
            return None

        out = self.adb.shell("getprop %s" % name)
        return out.decode("utf-8").strip()

    def reboot(self, restart_services=True):
        """Reboots the device and wait for device to complete booting.

        This is probably going to print some error messages in console. Only
        use if there's no other option.

        Raises:
            AndroidDeviceError is raised if waiting for completion timed
            out.
        """
        if self.isBootloaderMode:
            self.fastboot.reboot()
            return

        if restart_services:
            has_adb_log = self.isAdbLogcatOn
            has_vts_agent = True if self.vts_agent_process else False
            if has_adb_log:
                self.stopAdbLogcat()
            if has_vts_agent:
                self.stopVtsAgent()

        self.adb.reboot()
        self.waitForBootCompletion()
        self.rootAdb()

        if restart_services:
            if has_adb_log:
                self.startAdbLogcat()
            if has_vts_agent:
                self.startVtsAgent()

    def startServices(self):
        """Starts long running services on the android device.

        1. Start adb logcat capture.
        2. Start VtsAgent and create HalMirror unless disabled in config.
        3. If enabled in config, start sl4a service and create sl4a clients.
        """
        enable_vts_agent = getattr(self, "enable_vts_agent", True)
        enable_sl4a = getattr(self, "enable_sl4a", False)
        enable_sl4a_ed = getattr(self, "enable_sl4a_ed", False)
        try:
            self.startAdbLogcat()
        except:
            self.log.exception("Failed to start adb logcat!")
            raise
        if enable_vts_agent:
            self.startVtsAgent()
            self.device_command_port = int(
                self.adb.shell("cat /data/local/tmp/vts_tcp_server_port"))
            logging.info("device_command_port: %s", self.device_command_port)
            if not self.host_command_port:
                self.host_command_port = adb.get_available_host_port()
            self.adb.tcp_forward(self.host_command_port,
                                 self.device_command_port)
            self.hal = hal_mirror.HalMirror(self.host_command_port,
                                            self.host_callback_port)
            self.lib = lib_mirror.LibMirror(self.host_command_port)
            self.shell = shell_mirror.ShellMirror(self.host_command_port)
        if enable_sl4a:
            self.startSl4aClient(enable_sl4a_ed)

    def stopServices(self):
        """Stops long running services on the android device.
        """
        if self.adb_logcat_process:
            self.stopAdbLogcat()
        self.stopVtsAgent()
        if self.hal:
            self.hal.CleanUp()

    def startVtsAgent(self):
        """Start HAL agent on the AndroidDevice.

        This function starts the target side native agent and is persisted
        throughout the test run.
        """
        self.log.info("Starting VTS agent")
        if self.vts_agent_process:
            raise AndroidDeviceError(
                "HAL agent is already running on %s." % self.serial)

        cleanup_commands = [
            "rm -f /data/local/tmp/vts_driver_*",
            "rm -f /data/local/tmp/vts_agent_callback*"
        ]
        kill_commands = [
            "killall vts_hal_agent32", "killall vts_hal_agent64",
            "killall vts_hal_driver32", "killall vts_hal_driver64",
            "killall vts_shell_driver32", "killall vts_shell_driver64"
        ]
        cleanup_commands.extend(kill_commands)
        chmod_commands = [
            "chmod 755 %s/32/vts_hal_agent32" % DEFAULT_AGENT_BASE_DIR,
            "chmod 755 %s/64/vts_hal_agent64" % DEFAULT_AGENT_BASE_DIR,
            "chmod 755 %s/32/vts_hal_driver32" % DEFAULT_AGENT_BASE_DIR,
            "chmod 755 %s/64/vts_hal_driver64" % DEFAULT_AGENT_BASE_DIR,
            "chmod 755 %s/32/vts_shell_driver32" % DEFAULT_AGENT_BASE_DIR,
            "chmod 755 %s/64/vts_shell_driver64" % DEFAULT_AGENT_BASE_DIR
        ]
        cleanup_commands.extend(chmod_commands)
        for cmd in cleanup_commands:
            try:
                self.adb.shell(cmd)
            except adb.AdbError as e:
                self.log.warning(
                    "A command to setup the env to start the VTS Agent failed %s",
                    e)

        bits = ['64', '32'] if self.is64Bit else ['32']
        for bitness in bits:
            vts_agent_log_path = os.path.join(self.log_path,
                                              "vts_agent_" + bitness + ".log")
            cmd = (
                'adb -s {s} shell LD_LIBRARY_PATH={path}/{bitness} '
                '{path}/{bitness}/vts_hal_agent{bitness}'
                ' {path}/32/vts_hal_driver32 {path}/64/vts_hal_driver64 {path}/spec'
                ' {path}/32/vts_shell_driver32 {path}/64/vts_shell_driver64 >> {log} 2>&1'
            ).format(
                s=self.serial,
                bitness=bitness,
                path=DEFAULT_AGENT_BASE_DIR,
                log=vts_agent_log_path)
            try:
                self.vts_agent_process = utils.start_standing_subprocess(
                    cmd, check_health_delay=1)
                break
            except utils.VTSUtilsError as e:
                logging.exception(e)
                with open(vts_agent_log_path, 'r') as log_file:
                    logging.error("VTS agent output:\n")
                    logging.error(log_file.read())
                # one common cause is that 64-bit executable is not supported
                # in low API level devices.
                if bitness == '32':
                    raise
                else:
                    logging.error('retrying using a 32-bit binary.')

    def stopVtsAgent(self):
        """Stop the HAL agent running on the AndroidDevice.
        """
        if not self.vts_agent_process:
            return
        try:
            utils.stop_standing_subprocess(self.vts_agent_process)
        except utils.VTSUtilsError as e:
            logging.error("Cannot stop VTS agent. %s", e)
        self.vts_agent_process = None

    @property
    def product_type(self):
        """Gets the product type name."""
        return self._product_type

    # Code for using SL4A client
    def startSl4aClient(self, handle_event=True):
        """Create an sl4a connection to the device.

        Return the connection handler 'droid'. By default, another connection
        on the same session is made for EventDispatcher, and the dispatcher is
        returned to the caller as well.
        If sl4a server is not started on the device, try to start it.

        Args:
            handle_event: True if this droid session will need to handle
                          events.
        """
        self._sl4a_sessions = {}
        self._sl4a_event_dispatchers = {}
        if not self.sl4a_host_port or not adb.is_port_available(
                self.sl4a_host_port):
            self.sl4a_host_port = adb.get_available_host_port()
        self.adb.tcp_forward(self.sl4a_host_port, self.sl4a_target_port)
        try:
            droid = self._createNewSl4aSession()
        except sl4a_client.Error:
            sl4a_client.start_sl4a(self.adb)
            droid = self._createNewSl4aSession()
        self.sl4a = droid
        if handle_event:
            ed = self._getSl4aEventDispatcher(droid)
        self.sl4a_event = ed

    @property
    def sl4a(self):
        """The default SL4A session to the device if exist, None otherwise."""
        try:
            return self._sl4a_sessions[sorted(self._sl4a_sessions)[0]][0]
        except IndexError:
            return None

    @property
    def sl4as(self):
        """A list of the active SL4A sessions on this device.

        If multiple connections exist for the same session, only one connection
        is listed.
        """
        keys = sorted(self._sl4a_sessions)
        results = []
        for key in keys:
            results.append(self._sl4a_sessions[key][0])
        return results

    def getVintfXml(self, use_lshal=True):
        """Reads the vendor interface manifest Xml.

        Args:
            use_hal: bool, set True to use lshal command and False to fetch
                     /vendor/manifest.xml directly.

        Returns:
            Vendor interface manifest string.
        """
        try:
            if use_lshal:
                stdout = self.adb.shell('"lshal --init-vintf 2> /dev/null"')
                return str(stdout)
            else:
                stdout = self.adb.shell('cat /vendor/manifest.xml')
                return str(stdout)
        except adb.AdbError as e:
            return None

    def _getSl4aEventDispatcher(self, droid):
        """Return an EventDispatcher for an sl4a session

        Args:
            droid: Session to create EventDispatcher for.

        Returns:
            ed: An EventDispatcher for specified session.
        """
        # TODO (angli): Move service-specific start/stop functions out of
        # android_device, including VTS Agent, SL4A, and any other
        # target-side services.
        ed_key = self.serial + str(droid.uid)
        if ed_key in self._sl4a_event_dispatchers:
            if self._sl4a_event_dispatchers[ed_key] is None:
                raise AndroidDeviceError("EventDispatcher Key Empty")
            self.log.debug("Returning existing key %s for event dispatcher!",
                           ed_key)
            return self._sl4a_event_dispatchers[ed_key]
        event_droid = self._addNewConnectionToSl4aSession(droid.uid)
        ed = event_dispatcher.EventDispatcher(event_droid)
        self._sl4a_event_dispatchers[ed_key] = ed
        return ed

    def _createNewSl4aSession(self):
        """Start a new session in sl4a.

        Also caches the droid in a dict with its uid being the key.

        Returns:
            An Android object used to communicate with sl4a on the android
                device.

        Raises:
            sl4a_client.Error: Something is wrong with sl4a and it returned an
            existing uid to a new session.
        """
        droid = sl4a_client.Sl4aClient(port=self.sl4a_host_port)
        droid.open()
        if droid.uid in self._sl4a_sessions:
            raise sl4a_client.Error(
                "SL4A returned an existing uid for a new session. Abort.")
        logging.debug("set sl4a_session[%s]", droid.uid)
        self._sl4a_sessions[droid.uid] = [droid]
        return droid

    def _addNewConnectionToSl4aSession(self, session_id):
        """Create a new connection to an existing sl4a session.

        Args:
            session_id: UID of the sl4a session to add connection to.

        Returns:
            An Android object used to communicate with sl4a on the android
                device.

        Raises:
            DoesNotExistError: Raised if the session it's trying to connect to
            does not exist.
        """
        if session_id not in self._sl4a_sessions:
            raise DoesNotExistError("Session %d doesn't exist." % session_id)
        droid = sl4a_client.Sl4aClient(
            port=self.sl4a_host_port, uid=session_id)
        droid.open(cmd=sl4a_client.Sl4aCommand.CONTINUE)
        return droid

    def _terminateSl4aSession(self, session_id):
        """Terminate a session in sl4a.

        Send terminate signal to sl4a server; stop dispatcher associated with
        the session. Clear corresponding droids and dispatchers from cache.

        Args:
            session_id: UID of the sl4a session to terminate.
        """
        if self._sl4a_sessions and (session_id in self._sl4a_sessions):
            for droid in self._sl4a_sessions[session_id]:
                droid.closeSl4aSession()
                droid.close()
            del self._sl4a_sessions[session_id]
        ed_key = self.serial + str(session_id)
        if ed_key in self._sl4a_event_dispatchers:
            self._sl4a_event_dispatchers[ed_key].clean_up()
            del self._sl4a_event_dispatchers[ed_key]

    def _terminateAllSl4aSessions(self):
        """Terminate all sl4a sessions on the AndroidDevice instance.

        Terminate all sessions and clear caches.
        """
        if self._sl4a_sessions:
            session_ids = list(self._sl4a_sessions.keys())
            for session_id in session_ids:
                try:
                    self._terminateSl4aSession(session_id)
                except:
                    self.log.exception("Failed to terminate session %d.",
                                       session_id)
            if self.sl4a_host_port:
                self.adb.forward("--remove tcp:%d" % self.sl4a_host_port)
                self.sl4a_host_port = None


class AndroidDeviceLoggerAdapter(logging.LoggerAdapter):
    """A wrapper class that attaches a prefix to all log lines from an
    AndroidDevice object.
    """

    def process(self, msg, kwargs):
        """Process every log message written via the wrapped logger object.

        We are adding the prefix "[AndroidDevice|<serial>]" to all log lines.

        Args:
            msg: string, the original log message.
            kwargs: dict, the key value pairs that can be used to modify the
                    original log message.
        """
        msg = "[AndroidDevice|%s] %s" % (self.extra["serial"], msg)
        return (msg, kwargs)
