#!/usr/bin/env python3
#
# SPDX-License-Identifier: Apache-2.0
# Copyright Pionix GmbH and Contributors to EVerest
#
"""Pionix control module."""
import argparse
import logging
import json
from typing import Tuple
from jinja2 import Environment, FileSystemLoader
import yaml
import os
from pathlib import Path, PurePath
import subprocess
import sys
import shutil
import multiprocessing
import requests
import re
import datetime
from paho.mqtt import client as mqtt_client
from threading import Thread
import time
import dbus
import dotenv


log = logging.getLogger("pionix_control")

mqtt_client = mqtt_client.Client("pionix_control")

pionix_control_config_path = None
everest_environment_env_path = None
charger_info_path = None
charger_info = None

class Color:
    """Represents a subset of terminal color codes for use in log messages."""

    DEFAULT = ""
    CLEAR = "\033[0m"
    BLACK = "\033[30m"
    GREY = "\033[90m"
    WHITE = "\033[37m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    @classmethod
    def set_none(cls):
        """Remove the color codes for no-color mode."""
        Color.DEFAULT = ""
        Color.CLEAR = ""
        Color.BLACK = ""
        Color.GREY = ""
        Color.WHITE = ""
        Color.RED = ""
        Color.GREEN = ""
        Color.BLUE = ""
        Color.YELLOW = ""
        Color.MAGENTA = ""
        Color.CYAN = ""


class ColorFormatter(logging.Formatter):
    """Logging formatter that uses pre-configured colors for different logging levels."""

    def __init__(self, color=True, formatting_str="[%(name)s]: %(message)s"):
        """Initialize the ColorFormatter."""
        super().__init__()
        self.color = color
        if not color:
            Color.set_none()
        self.formatting_str = formatting_str
        self.colored_formatting_strings = {
            logging.DEBUG: self.build_colored_formatting_string(Color.GREY),
            logging.INFO: self.build_colored_formatting_string(Color.CLEAR),
            logging.WARNING: self.build_colored_formatting_string(Color.YELLOW),
            logging.ERROR: self.build_colored_formatting_string(Color.RED),
            logging.CRITICAL: self.build_colored_formatting_string(Color.MAGENTA),
        }

    def build_colored_formatting_string(self, color: Color) -> str:
        """Build a formatting string with the provided color."""
        if self.color:
            return f"{color}{self.formatting_str}{Color.CLEAR}"
        return f"{self.formatting_str}"

    def format(self, record):
        """Format a record with the colored formatter."""
        return logging.Formatter(self.colored_formatting_strings[record.levelno]).format(record)


def quote(lst: list) -> list:
    """Put quotation marks around every list element, which is assumed to be a str."""
    return [f"\"{element}\"" for element in lst]


def prettify(lst: list, indent: int) -> str:
    """Construct string from list elements with the given indentation."""
    output = ""
    space = " " * indent
    for out in lst:
        if out and out != "\n":
            if len(output) > 0:
                output += f"\n{space}{out}"
            else:
                output += f"{space}{out}"
    return output


def pretty_print(lst: list, indent: int, log_level: int):
    """Debug log every list element with the given indentation."""
    space = " " * indent
    for out in lst:
        if out and out != "\n":
            if log_level == logging.DEBUG:
                log.debug(f"{space}{out}")
            elif log_level == logging.INFO:
                log.info(f"{space}{out}")
            elif log_level == logging.WARNING:
                log.warning(f"{space}{out}")
            elif log_level == logging.ERROR:
                log.error(f"{space}{out}")
            elif log_level == logging.CRITICAL:
                log.critical(f"{space}{out}")
            else:
                log.info(f"{space}{out}")


def pretty_print_process(c: subprocess.CompletedProcess, indent: int, log_level: int):
    """Pretty print stdout and stderr of a CompletedProcess object."""
    stdout = c.stdout.decode("utf-8").split("\n")
    pretty_print(stdout, indent, log_level)

    stderr = c.stderr.decode("utf-8").split("\n")
    pretty_print(stderr, indent, log_level)


def pattern_matches(string: str, patterns: list) -> bool:
    """Return true if one of the patterns match with the string, false otherwise."""
    matches = False
    for pattern in patterns:
        if PurePath(string).match(pattern):
            log.debug(f"Pattern \"{pattern}\" accepts string \"{string}\"")
            matches = True
            break
    return matches


def main_handler(args):
    working_dir = Path(args.working_dir).expanduser().resolve()
    metadata_dir = Path(args.metadata_dir).expanduser().resolve()

    global pionix_control_config_path
    global everest_environment_env_path
    pionix_control_config_path = metadata_dir / "everest_configs.yaml"
    everest_environment_env_path = metadata_dir / "everest_environment.env"
    if args.charger_info:
        global charger_info_path
        charger_info_path = Path(args.charger_info).expanduser().resolve()
        log.info(f"Using charger info file: {charger_info_path}")


def get_parser(version) -> argparse.ArgumentParser:
    """Return the argument parser containing all command line options."""
    parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter,
                                     description="Pionix Control")
    parser.add_argument('--version', action='version', version=f'%(prog)s {version}')
    parser.add_argument("--working_dir", metavar='WORKINGDIR', default=".",
                        help="Working directory, default is the current one.", required=False)
    parser.add_argument("--metadata-dir", metavar='METADATADIR', default="/mnt/user_data",
                        help="Metadata directory.", required=False)
    parser.add_argument("--charger-info", metavar='METADATADIR', default="",
                        help="Charger information file.", required=False)
    parser.add_argument(
        "--verbose", action="store_true",
        help="Verbose output.")
    parser.add_argument(
        "--nocolor", action="store_true",
        help="No color output.")

    parser.set_defaults(action_handler=main_handler)

    return parser


def setup_logging(verbose: bool, nocolor: bool):
    """Setup logging, choosing logger level and if colorful log output is requested."""
    if verbose:
        log.setLevel(level=logging.DEBUG)
    else:
        log.setLevel(level=logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColorFormatter(color=not nocolor))
    log.addHandler(console_handler)

    if not nocolor:
        log.debug(
            "Using \033[1;31mc\033[1;33mo\033[93ml\033[92mo\033[94mr\033[34mf\033[95mu\033[35ml\033[0m \033[1m"
            "output\033[0m")


stop_cmd_topic = "everest_api/control/cmd/stop"
restart_cmd_topic = "everest_api/control/cmd/restart"
restart_display_app_cmd_topic = "everest_api/control/cmd/restart_display_app"
status_cmd_topic = "everest_api/control/cmd/status"
get_selected_config_cmd_topic = "everest_api/control/cmd/get_selected_config"
change_config_cmd_topic = "everest_api/control/cmd/change_config"
get_config_paths_cmd_topic = "everest_api/control/cmd/get_config_paths"
status_var_topic = "everest_api/control/var/status"
available_var_topic = "everest_api/control/var/available"
everest_api_info_var_info = "everest_api/info/var/info"
config_paths_var_topic = "everest_api/control/var/config_paths"
selected_config_var_topic = "everest_api/control/var/selected_config"
info_var_topic = "everest_api/info/var/info"

unit_names = ["everest-control.service"]
config = None

bus = dbus.SystemBus()
systemd = bus.get_object(
    'org.freedesktop.systemd1',
    '/org/freedesktop/systemd1'
)

manager = dbus.Interface(
    systemd,
    'org.freedesktop.systemd1.Manager'
)

pub_thread = None


def load_pionix_control_config():
    config = None
    if pionix_control_config_path.exists():
        # load config if exists
        log.debug(f"Loading PionixControl config from {pionix_control_config_path}")
        with open(pionix_control_config_path, encoding='utf-8') as pionix_control_config_file:
            try:
                config = yaml.safe_load(pionix_control_config_file)
            except yaml.YAMLError as e:
                log.error(f"Error parsing yaml of \"{pionix_control_config_path}\": {e}")
    return config


def load_charger_info():
    info = None
    if charger_info_path and charger_info_path.exists():
        # load charger_info if exists
        log.debug(f"Loading Charger Info from {charger_info_path}")
        with open(charger_info_path, encoding='utf-8') as charger_info_file:
            try:
                info = yaml.safe_load(charger_info_file)
            except yaml.YAMLError as e:
                log.error(f"Error parsing yaml of \"{charger_info_path}\": {e}")
    return info


def publish_repeatedly():
    path_to_the_charger_info_file = {
        "charger_serial_number": "0123",
        "firmware_version": "v0.1.2",
        "hardware_version": "v0.1.2",
        "model_name": "MicroMegaWattCharger",
        "pcb_serial_number": "123"
    }
    while True:  # FIXME
        mqtt_client.publish(available_var_topic, "true")
        if charger_info:
            mqtt_client.publish(info_var_topic, json.dumps(charger_info))
        time.sleep(1.0)


def dbus_get_unit_state(unit_name):
    service = bus.get_object('org.freedesktop.systemd1',
                             object_path=manager.GetUnit(unit_name))
    interface = dbus.Interface(service,
                               dbus_interface='org.freedesktop.DBus.Properties')
    unit_state = interface.Get('org.freedesktop.systemd1.Unit', 'ActiveState')
    return unit_state


def stop_everest_systemd():
    log.info(f"Stopping all units: {', '.join(unit_names)}")
    for unit_name in unit_names:
        ret = manager.StopUnit(unit_name, "fail")
        log.info(f"{unit_name} ret: {ret}")


def restart_everest_control_systemd():
    stop_everest_systemd()
    log.info("restarting")
    unit_name = "everest-control.service"
    manager.ResetFailedUnit(unit_name);
    ret = manager.RestartUnit(unit_name, "replace")
    log.info(f"ret: {ret}")


def restart_display_app():
    log.info("restarting display app")
    unit_name = "display-app.service"
    ret = manager.RestartUnit(unit_name, "replace")
    log.info(f"ret: {ret}")


def publish_selected_config():
    cfg = dotenv.get_key(dotenv_path=everest_environment_env_path, key_to_get="EVEREST_CONFIG")
    if not cfg:
        cfg = "unknown"
    mqtt_client.publish(selected_config_var_topic, cfg)


def on_connect(mqtt_client, obj, flags, rc, properties=None):
    log.info("connected")
    # now publish that control is available
    pub_thread = Thread(target=publish_repeatedly)
    pub_thread.start()


def on_message(mqtt_client, obj, msg):
    log.info(f"{msg.topic} {msg.payload}")
    if msg.topic == status_cmd_topic:
        # received status, do the dbus stuff
        states = {}
        for unit_name in unit_names:
            try:
                unit_state = dbus_get_unit_state(unit_name)
            except dbus.exceptions.DBusException:
                unit_state = "not_loaded"
            log.info(f"Unit \"{unit_name}\" state: {unit_state}")
            states[unit_name] = unit_state
        states_json = json.dumps(states)
        mqtt_client.publish(status_var_topic, states_json)
    elif msg.topic == stop_cmd_topic:
        stop_everest_systemd()
    elif msg.topic == restart_cmd_topic:
        restart_everest_control_systemd()
    elif msg.topic == restart_display_app_cmd_topic:
        restart_display_app()
    elif msg.topic == change_config_cmd_topic:
        # changing config
        log.info("changing config")
        try:
            data = json.loads(msg.payload)
            log.info(f"Changing config to {data['config_path']}")
            dotenv.set_key(dotenv_path=everest_environment_env_path,
                           key_to_set="EVEREST_CONFIG", value_to_set=data["config_path"])
            publish_selected_config()
            restart_everest_control_systemd()
        except json.JSONDecodeError as e:
            log.error(f"JSON decoding error: {e}")
    elif msg.topic == get_selected_config_cmd_topic:
        log.info("Getting selected config")
        publish_selected_config()
    elif msg.topic == get_config_paths_cmd_topic:
        log.info("getting config paths")
        config = load_pionix_control_config()
        config_paths = {}
        config_paths['configs'] = config['configs']
        for cfg in config["configs"]:
            log.info(f"Found explicity set config {cfg}")
        config_paths['config_dirs'] = {}
        for cfg_dir in config["config_dirs"]:
            config_paths['config_dirs'][cfg_dir] = []
            log.info(f"Found config dir {cfg_dir}")
            files = [f for f in Path(cfg_dir).iterdir() if f.is_file() and f.suffix == ".yaml"]
            log.info(f"Files: {files}")
            for f in files:
                config_paths['config_dirs'][cfg_dir].append(f.name)

        config_paths_json = json.dumps(config_paths)
        mqtt_client.publish(config_paths_var_topic, config_paths_json)


def main(parser: argparse.ArgumentParser):
    """The main entrypoint of pionix_control. Provides different functionality based on the given command line arguments."""
    args = parser.parse_args()

    setup_logging(args.verbose, args.nocolor)
    args.action_handler(args)
    global config
    config = load_pionix_control_config()
    global charger_info
    charger_info = load_charger_info()
    if not everest_environment_env_path.exists():
        log.info(f"{everest_environment_env_path} did not exist, created a empty file")
        everest_environment_env_path.touch()

    mqtt_server_uri = "localhost"
    mqtt_server_port = 1883
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(mqtt_server_uri, mqtt_server_port)
    mqtt_client.subscribe(stop_cmd_topic)
    mqtt_client.subscribe(restart_cmd_topic)
    mqtt_client.subscribe(status_cmd_topic)
    mqtt_client.subscribe(change_config_cmd_topic)
    mqtt_client.subscribe(get_config_paths_cmd_topic)
    mqtt_client.subscribe(get_selected_config_cmd_topic)
    mqtt_client.subscribe(restart_display_app_cmd_topic)
    mqtt_client.loop_forever()
