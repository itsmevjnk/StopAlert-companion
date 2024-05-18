#!/usr/bin/python3

import sys
import os
import shutil
import math
import ptvgtfs as ptv # for downloading GTFS dataset from PTV (see ptvgtfs.py)
from gtfs import GTFS # for parsing GTFS feeds (see gtfs.py)
from datetime import datetime, timezone
from itertools import chain
import struct # for converting data into bytes
import re # regular expression
import serial # for device connections
import serial.tools.list_ports
from timeit import default_timer as timer # for timing

# set up argument parsing
import argparse
parser = argparse.ArgumentParser(
    description='PTV PC companion for the PT stop alert device'
)

# execution settings
parser.add_argument('-e', '--edit-action', help='edit action before execution', action='store_true')
parser.add_argument('-v', '--verbose', help='print more information during execution', action='store_true')

# action (generate FS only/upload only/generate and upload FS (default)/etc.)
group_action = parser.add_mutually_exclusive_group()
group_action.add_argument('-ld', '--list-devices', help=f'list all serial devices detected by the system', action='store_true')
group_action.add_argument('-go', '--generate-only', help='generate file system only', action='store_true')
group_action.add_argument('-uo', '--upload-only', help='upload existing file system only', action='store_true')
group_action.add_argument('-lf', '--list-files', help='list files on SPIFFS file system only instead of uploading', action='store_true')
group_action.add_argument('-df', '--dump-files', help='dump current file system on device to specified SPIFFS file system path instead of uploading', action='store_true')
group_action.add_argument('-lm', '--list-modes', help='list available public transport modes', action='store_true')
group_action.add_argument('-lr', '--list-routes', help='list available routes for the specified public transport mode')

# device settings
default_ser_port = 'COM1' if os.name == 'nt' else '/dev/ttyUSB0' # determine default serial port (COM1 on Windows, or /dev/ttyUSB0 on anything else)
parser.add_argument('-d', '--device', help=f'device serial port (defaults to {default_ser_port})', default=default_ser_port)
parser.add_argument('-b', '--baud', help='device serial communication baud rate (defaults to 115200)', type=int, default=115200) # we only need to touch this if we change the device's firmware to run UART at a different baud rate
parser.add_argument('-to', '--timeout', help='device serial communication timeout duration in seconds (defaults to 10)', type=int, default=10)
parser.add_argument('-frto', '--format-req-timeout', help='reformat request timeout duration in seconds (defaults to 30)', type=int, default=30)
parser.add_argument('-fto', '--format-timeout', help='reformat timeout duration in seconds (defaults to 60)', type=int, default=60)

# file system settings
parser.add_argument('-fs', '--filesystem', help='path to SPIFFS file system (defaults to fs/)', default='fs')
parser.add_argument('-nf', '--no-format', help='delete all files on device instead of clean reformatting', action='store_true')

# dataset generation settings
parser.add_argument('-u', '--dataset-url', help='PTV timetable/geolocation GTFS dataset URL (defaults to data.ptv.vic.gov.au source)', default='https://data.ptv.vic.gov.au/downloads/gtfs.zip')
parser.add_argument('-r', '--routes', help='select routes for inclusion in device file system (e.g. -r MetroBus:767,903,201) - use argument separately for each mode', action='append')

args = parser.parse_args() # parse arguments

# list of PT modes (based on ptvgtfs._modes)
PT_MODES = {
    'RegionalTrain': 'Regional train',
    'MetroTrain': 'Metropolitan train',
    'MetroTram': 'Metropolitan tram',
    'MetroBus': 'Metropolitan bus',
    'RegionalCoach': 'Regional coach',
    'RegionalBus': 'Regional bus',
    'Interstate': 'Interstate bus',
    'SkyBus': 'SkyBus'
}

# determine actions to be performed
ACT_LIST_FS: bool = args.list_files
ACT_DUMP_FS: bool = args.dump_files
ACT_RETRIEVE_FS_LISTING: bool = ACT_LIST_FS or ACT_DUMP_FS # get filesystem listing from device
ACT_GENERATE: bool = True if not args.generate_only and not args.upload_only and not ACT_RETRIEVE_FS_LISTING else args.generate_only
ACT_UPLOAD: bool = True if not args.generate_only and not args.upload_only and not ACT_RETRIEVE_FS_LISTING else args.upload_only
ACT_CONNECT_DEV: bool = ACT_RETRIEVE_FS_LISTING or ACT_UPLOAD # connect to device

# get other arguments
EDIT_ACTION: bool = args.edit_action # edit before executing
VERBOSE: bool = args.verbose # verbose output
DEVICE_PORT: str = args.device # device port path
DEVICE_BAUD: int = args.baud # device baud rate
DEVICE_TIMEOUT: int = args.timeout # device timeout
FS_PATH: str = args.filesystem # SPIFFS file system path
DATASET_URL: str = args.dataset_url # dataset URL
NO_REFORMAT: bool = args.no_format # delete all instead of reformat
REFORMAT_REQ_TIMEOUT: int = args.format_req_timeout # reformat request timeout
REFORMAT_TIMEOUT: int = args.format_timeout # reformat timeout (after request has been confirmed)

def port_description(port) -> str: # helper function to generate port description string
    return port.description + (f' (USB {port.vid:04X}:{port.pid:04X} S/N {port.serial_number} @ {port.location})' if port.vid is not None else '')
if args.list_devices:
    # list serial ports
    ports = serial.tools.list_ports.comports(True)
    print('Available serial ports:')
    for port in ports:
        print(f' - {port.device}: {port_description(port)}')
    exit()

if args.list_modes:
    # list available transport modes
    print('Available public transport modes: ' + ', '.join(PT_MODES.keys()) + '.')
    print('NOTE: Only MetroTram and MetroBus are tested with the device!')
    exit()

if args.list_routes is not None:
    # list available routes on the given network
    mode: str = args.list_routes

    if not mode in PT_MODES:
        print(f'[ERR] Invalid public transport mode {mode}')
        exit(1)
    
    if not os.path.isdir(mode): ptv.download_datasets(mode) # download dataset if it does not exist already
    routes = GTFS(mode).get_routes() # open dataset and get routes list

    # display routes
    print(f'The {PT_MODES[mode]} network consists of {len(routes)} route(s):')
    for route in routes:
        print(f' - {route}: {routes[route][0]}' + ('' if not VERBOSE else f' (route IDs: {', '.join(routes[route][1])})'))
    
    exit()

# process routes list
ROUTES: dict[str, list[str]] = dict()
if args.routes is not None:
    mode: str # type hint
    for mode in args.routes:
        mode_pair = mode.split(':') # split into network name and routes
        if len(mode_pair) != 2:
            print(f'[ERR] Malformed route specifier {mode}.')
            exit(1)
        if not mode_pair[0] in PT_MODES:
            print(f'[ERR] Public transport mode {mode_pair[0]} does not exist!')
            exit(1)
        routes = mode_pair[1].split(',')
        if len(routes) > 0: ROUTES[mode_pair[0]] = routes # add to routes

if EDIT_ACTION:
    # edit action before execution
    def menu(options: dict[int, str]) -> int: # helper function to display menu and read option from user
        for num in options:
            print(f'{num}. {options[num]}')
        while True:
            try:
                option = int(input('Enter your option: ').strip())
                if option in options:
                    return option
            except ValueError:
                pass
            print('Please enter a valid option.') # try again
    
    while True:
        # main menu
        print('\nStopAlert PC companion software')
        option = menu({
            1: 'Select operation',
            2: 'Device settings',
            3: 'File system settings',
            4: 'Dataset generation settings (incl. routes)',
            5: ('Disable' if VERBOSE else 'Enable') + ' verbose output',
            6: 'Save changes',
            0: 'Exit'
        })
        if option == 0: exit() # user chose to exit from program
        if option == 6: break # commit changes

        if option == 1: # select operation
            while True:
                # figure out what operation we're doing
                if ACT_GENERATE and ACT_UPLOAD:
                    operation = 1 # generate and upload FS (default)
                elif ACT_GENERATE:
                    operation = 2 # generate FS only
                elif ACT_UPLOAD:
                    operation = 3 # upload FS only
                elif ACT_LIST_FS:
                    operation = 4 # list FS contents
                elif ACT_DUMP_FS:
                    operation = 5 # dump device FS
                else:
                    print(f'[ERR] Unsupported action combination: ACT_GENERATE={ACT_GENERATE}, ACT_UPLOAD={ACT_UPLOAD}, ACT_LIST_FS={ACT_LIST_FS}, ACT_DUMP_FS={ACT_DUMP_FS}')
                    exit()
                
                # display menu
                print('\nOperation selection')
                operation_options = {
                    1: 'Generate and upload file system to device',
                    2: 'Generate file system to root directory only',
                    3: 'Upload existing file system from root directory only',
                    4: 'List device\'s file system contents',
                    5: 'Dump device\'s file system contents',
                    0: 'Go back'
                }
                for idx in operation_options:
                    if idx == operation: operation_options[idx] += ' (selected)'
                option = menu(operation_options)
                if option == 0: break

                # set action according to option
                if option == 1:
                    ACT_GENERATE = True
                    ACT_UPLOAD = True
                    ACT_LIST_FS = False
                    ACT_DUMP_FS = False
                elif option == 2:
                    ACT_GENERATE = True
                    ACT_UPLOAD = False
                    ACT_LIST_FS = False
                    ACT_DUMP_FS = False
                elif option == 3:
                    ACT_GENERATE = False
                    ACT_UPLOAD = True
                    ACT_LIST_FS = False
                    ACT_DUMP_FS = False
                elif option == 4:
                    ACT_GENERATE = False
                    ACT_UPLOAD = False
                    ACT_LIST_FS = True
                    ACT_DUMP_FS = False
                elif option == 5:
                    ACT_GENERATE = False
                    ACT_UPLOAD = False
                    ACT_LIST_FS = False
                    ACT_DUMP_FS = True
                # re-determine derived action settings
                ACT_RETRIEVE_FS_LISTING = ACT_LIST_FS or ACT_DUMP_FS 
                ACT_CONNECT_DEV = ACT_RETRIEVE_FS_LISTING or ACT_UPLOAD

        elif option == 2: # device settings
            while True:
                print('\nDevice settings')
                option = menu({
                    1: 'Set device port',
                    2: 'Set device baud rate',
                    3: 'Set communication timeout',
                    4: 'Set reformat request timeout',
                    5: 'Set reformat operation timeout',
                    0: 'Go back'
                })
                if option == 0: break

                if option == 1: # set device port
                    print(f'\nDevice port is currently set to {DEVICE_PORT}.')
                    print('Select another serial port from the list below:')
                    
                    ports = serial.tools.list_ports.comports(True)
                    port_options = {(x + 1): f'{port.device} ({port_description(port)})' for x, port in enumerate(ports)}
                    port_options[len(ports) + 1] = 'Enter manually'
                    port_options[0] = 'Go back'
                    
                    option = menu(port_options)
                    if option == 0: break
                    
                    if option <= len(ports):
                        DEVICE_PORT = ports[option - 1].device
                    else: # manual port selection
                        new_port = input('Enter the new device serial port (e.g. COM1 for Windows, or /dev/ttyUSB0 for Unix/Linux) (or press Enter to keep current setting): ').strip()
                        if len(new_port) > 0: DEVICE_PORT = new_port
                elif option == 2: # set device baud rate
                    print(f'\nDevice communication baud rate is currently set to {DEVICE_BAUD}. This setting should ONLY be changed if the device\'s firmware was recompiled for a different baud rate.')
                    while True:
                        new_baud = input('Enter the new baud rate (or press Enter to keep current setting): ').strip()
                        if len(new_baud) == 0: break # keep current setting
                        try:
                            new_baud = int(new_baud)
                            if new_baud > 0:
                                DEVICE_BAUD = new_baud
                                break
                        except ValueError:
                            pass
                        print(f'Please enter a valid baud rate.')
                elif option == 3: # set comm timeout
                    print(f'\nCommunication timeout is currently set to {DEVICE_TIMEOUT} sec.')
                    while True:
                        new_timeout = input('Enter the new timeout duration (or press Enter to keep current setting): ').strip()
                        if len(new_timeout) == 0: break
                        try:
                            new_timeout = int(new_timeout)
                            if new_timeout > 0:
                                DEVICE_TIMEOUT = new_timeout
                                break
                        except ValueError:
                            pass
                        print(f'Please enter a valid duration.')
                elif option == 4: # set reformat request timeout
                    print(f'\nReformat request timeout is currently set to {REFORMAT_REQ_TIMEOUT} sec. This setting should ONLY be changed if the device\'s firmware was recompiled with a different duration.')
                    while True:
                        new_timeout = input('Enter the new timeout duration (or press Enter to keep current setting): ').strip()
                        if len(new_timeout) == 0: break
                        try:
                            new_timeout = int(new_timeout)
                            if new_timeout > 0:
                                REFORMAT_REQ_TIMEOUT = new_timeout
                                break
                        except ValueError:
                            pass
                        print(f'Please enter a valid duration.')
                elif option == 5: # set reformat operation timeout
                    print(f'\nReformat operation timeout is currently set to {REFORMAT_TIMEOUT}.')
                    while True:
                        new_timeout = input('Enter the new timeout duration (or press Enter to keep current setting): ').strip()
                        if len(new_timeout) == 0: break
                        try:
                            new_timeout = int(new_timeout)
                            if new_timeout > 0:
                                REFORMAT_TIMEOUT = new_timeout
                                break
                        except ValueError:
                            pass
                        print(f'Please enter a valid duration.')

        elif option == 3: # FS settings
            while True:
                print('\nFile system settings')
                option = menu({
                    1: 'Change path to file system root (on this PC)',
                    2: ('Disable' if NO_REFORMAT else 'Enable') + ' deleting all files on device instead of reformatting',
                    0: 'Go back'
                })
                if option == 0: break

                if option == 1: # change SPIFFS root path
                    print(f'\nThe PC companion software will store the data to be uploaded to the device at \'{FS_PATH}\'.')
                    new_path = input('Enter the new path (or press Enter to keep current setting): ').strip()
                    if len(new_path) > 0: FS_PATH = new_path
                elif option == 2: # toggle deleting all files
                    NO_REFORMAT = not NO_REFORMAT
                    if NO_REFORMAT:
                        print('The software will issue commands to delete all files from the device\'s file system (similar to \'rm -rf /\') prior to uploading.')
                    else:
                        print('The software will issue commands to reformat the device\'s file system prior to uploading.')
        elif option == 4: # data generation settings
            while True:
                print('\nDataset generation settings')
                option = menu({
                    1: 'Set GTFS dataset URL',
                    2: 'Select route(s)',
                    0: 'Go back'
                })
                if option == 0: break # go back to main menu
                
                if option == 1: # set dataset URL
                    print(f'\nThe dataset URL is currently set to \'{DATASET_URL}\'.')
                    new_url = input('Enter the new URL (or press Enter to keep current setting): ').strip()
                    if len(new_url) > 0: DATASET_URL = new_url
                elif option == 2: # select route(s)
                    while True:
                        # select transport mode
                        print(f'\nPlease select the transport mode whose route list you want to modify:')
                        mode_options = {(x + 1): f'{PT_MODES[key]} ({len(ROUTES[key]) if key in ROUTES else 0} route(s))' for x, key in enumerate(PT_MODES)}
                        mode_options[0] = 'Go back'
                        option = menu(mode_options)
                        if option == 0: break
                        
                        # select route(s) for inclusion
                        mode = list(PT_MODES.keys())[option - 1]
                        if mode not in ROUTES:
                            print(f'\nMode \'{PT_MODES[mode]}\' does not have any routes included for generation.')
                        else:
                            print(f'\nMode \'{PT_MODES[mode]}\' has {len(ROUTES[mode])} route(s) included for generation: ' + ','.join(ROUTES[mode]) + '.')
                        new_routes = ''.join(input('Enter routes to be included in dataset separated by spaces (or press Enter to remove all routes): ').split()) # remove all whitespaces
                        if len(new_routes) == 0:
                            ROUTES.pop(mode, None)
                            print(f'All routes have been removed from the mode.')
                        else:
                            ROUTES[mode] = list(set(new_routes.split(','))) # remove duplicate items by converting to set and then back to list
                            print(f'Added {len(ROUTES[mode])} route(s) to mode.')
        elif option == 5: # toggle verbose
            VERBOSE = not VERBOSE
    
    # generate command
    if os.environ.get('ENV_DOCKER', False): # using Docker
        command = 'docker run --privileged --volume /dev:/dev -it pc-companion'
    else:
        command = sys.argv[0]
    if VERBOSE: command += ' -v' # enable verbose output
    if not (ACT_GENERATE and ACT_UPLOAD): # we only need the action arg if we're not using the default action
        if ACT_GENERATE:
            command += ' -go'
        elif ACT_UPLOAD:
            command += ' -uo'
        elif ACT_LIST_FS:
            command += ' -lf'
        elif ACT_DUMP_FS:
            command += ' -df'
    if ACT_CONNECT_DEV: # we only need device settings if we connect to the device
        command += f' -d {DEVICE_PORT} -b {DEVICE_BAUD} -to {DEVICE_TIMEOUT}'
        if not NO_REFORMAT: command += f' -frto {REFORMAT_REQ_TIMEOUT} -fto {REFORMAT_TIMEOUT}' # include reformat timeouts if we reformat the device
        else: command += ' -nf'
    if ACT_GENERATE: # we only need dataset settings if we generate data
        command += f' -u {DATASET_URL}'
        command += ''.join([f' -r {mode}:{','.join(ROUTES[mode])}' for mode in ROUTES])
    if ACT_GENERATE or ACT_UPLOAD or ACT_DUMP_FS: # add FS path if we need it
        command += f' -fs {FS_PATH}'
    print('The configured action can be performed using the following command:')
    print(f'  {command}')

# helper function to prepare empty file system root directory
def prepare_fs():
    if not os.path.exists(FS_PATH):
        # given path does not exist
        if VERBOSE: print(f'[DBG] Creating SPIFFS root directory at {FS_PATH}.')
        os.mkdir(FS_PATH)
    elif not os.path.isdir(FS_PATH):
        # given path is not a directory
        print(f'[ERR] {FS_PATH} is not a directory!')
        exit(1)
    
    if VERBOSE: print(f'[DBG] Removing contents of {FS_PATH}.')
    for root, dirs, files in os.walk(FS_PATH):
        for f in files: os.unlink(os.path.join(root, f))
        for d in dirs: shutil.rmtree(os.path.join(root, d))

if ACT_GENERATE: # generate file system  
    if len(ROUTES) == 0:
        print(f'[ERR] No routes specified to generate dataset for!')
        exit(1)

    prepare_fs()
    
    update_datestamp = datetime.now(timezone.utc).strftime('%Y%m%d') # update datestamp
    ptv.download_datasets(ROUTES.keys()) # download datasets for specified modes
    if VERBOSE: print(f'[DBG] Dataset update datestamp: {update_datestamp}')

    for mode in ROUTES:
        mode_routes = ROUTES[mode]
        mode_id = ptv._modes[mode]

        print(f'Generating dataset for {PT_MODES[mode]} (ID {mode_id}) route(s) {', '.join(mode_routes)}.')

        # create dataset directory
        print(' - Creating dataset directory.')
        mode_path = os.path.join(FS_PATH, str(mode_id))
        os.mkdir(mode_path)

        network = GTFS(mode) # open GTFS dataset

        # get list of routes
        print(' - Getting list of routes.')
        routes_info = network.get_routes()
        if not set(mode_routes).issubset(routes_info.keys()):
            print('[ERR] There is an invalid route number for the network!')
            exit(1)

        # get list of stops
        print(' - Getting list of stops.')
        stops = network.get_stops()
        if VERBOSE: print(f'[DBG] {mode} has {len(stops)} stop(s).')

        # get stop patterns for specified routes
        print(' - Getting stopping patterns.')
        patterns: dict[str, dict[str, list[str]]] = network.get_route_stop_pattern(mode_routes, resolve_direction_name=True)
        if VERBOSE: # print stopping patterns
            for route in patterns:
                for direction in patterns[route]:
                    print(f'[DBG] {route} to {direction}: {','.join(patterns[route][direction])} ({len(patterns[route][direction])} stop(s))')

        # filter stops list
        stop_ids = set(chain.from_iterable([patterns[route][direction] for route in patterns for direction in patterns[route]])) # set of stop IDs to keep
        stops_filtered = {id: stops[id] for id in stop_ids} # filter stops into new dict
        if VERBOSE: print(f'[DBG] Stops filtered for inclusion: {','.join(stops_filtered)} ({len(stops_filtered)} stop(s))')

        # generate stops file contents
        print(' - Generating stops and stops.map file contents.')
        stops_dat = b''
        stops_map = b''
        for id in stops_filtered:
            stop = stops_filtered[id]
            stops_map += struct.pack('<L', len(stops_dat)) # save stops file EOF offset to stops.map
            stops_dat += struct.pack('<f', stop.coords.latitude * math.pi / 180.0) # latitude (converted from deg to rad)
            stops_dat += struct.pack('<f', stop.coords.longitude * math.pi / 180.0) # longitude (converted from deg to rad)
            stops_dat += re.sub(r'\(.*\)$', '', stop.name).strip().encode('ascii') # stop name (removed suburb name, stripped to remove trailing whitespaces, then ASCII encoded)
            stops_dat += b'\0' # null termination
        if VERBOSE: print(f'[DBG] File sizes: stops: {len(stops_dat)}, stops.map: {len(stops_map)}')

        # write stops and stops.map
        print(' - Writing stops to disk.')
        with open(os.path.join(mode_path, 'stops'), 'wb') as f: f.write(stops_dat)
        print(' - Writing stops.map to disk.')
        with open(os.path.join(mode_path, 'stops.map'), 'wb') as f: f.write(stops_map)

        # write routes list
        print(' - Writing routes to disk.')
        with open(os.path.join(mode_path, 'routes'), 'w', newline='\n') as f:
            f.writelines([num + '\n' for num in patterns])

        # remap stops to index
        print(' - Creating stop index mapping.')
        stops_idx = {id: idx for idx, id in enumerate(stops_filtered)} # mapping from stop ID to stop index
        if VERBOSE: print(f'[DBG] Stop ID-index mapping: {','.join([f'{id}->{stops_idx[id]}' for id in stops_idx])}')

        # generate route data
        for route_num in patterns:
            pattern = patterns[route_num] # stopping pattern for each direction

            print(f' - Generating data for the {route_num} route.')
            route_path = os.path.join(mode_path, 'route_data', route_num)
            os.makedirs(route_path)

            # write route info file
            print(f'    - Writing info file to disk.')
            dir0, dir1 = pattern # unpack direction names
            def fix_dir(dir: str) -> str: # tram routes seem to state their directions as X to Y which can cause some confusion
                dir_split = dir.split()
                try:
                    to_idx = [x.lower() for x in dir_split].index('to')
                    dir = ' '.join(dir_split[to_idx + 1:])
                except ValueError: pass # no need to fix since there's no 'to' in the direction name
                return dir
            with open(os.path.join(route_path, 'info'), 'w', newline='\n') as f:
                f.write(
                    routes_info[route_num][0] + '\n' + 
                    fix_dir(dir0) + '\n' +
                    fix_dir(dir1) + '\n'
                )
            
            def prep_write_seq(fname: str, stops: list[str]): # helper method to prepare and write seq files
                print(f'    - Generating {fname} file contents.')
                seq_dat = b''
                for stop in stops: seq_dat += struct.pack('<H', stops_idx[stop])
                if VERBOSE: print(f'[DBG] {fname} file size: {len(seq_dat)} bytes')
                
                print(f'    - Writing {fname} to disk.')
                with open(os.path.join(route_path, fname), 'wb') as f: f.write(seq_dat)
            prep_write_seq('seq0', pattern[dir0])
            prep_write_seq('seq1', pattern[dir1])
    
    # generate networks file
    print('Writing networks file to disk.')
    with open(os.path.join(FS_PATH, 'networks'), 'w', newline='\n') as f:
        f.writelines([f'{ptv._modes[mode]}:{update_datestamp}:{PT_MODES[mode]}\n' for mode in ROUTES])
    
if ACT_CONNECT_DEV:
    print(f'Opening serial communication on {DEVICE_PORT} at {DEVICE_BAUD} bps.')
    device = serial.Serial(DEVICE_PORT, DEVICE_BAUD, timeout=DEVICE_TIMEOUT)

    # helper function to wait until device is ready
    def wait_ready():
        result = device.read()
        if len(result) == 0:
            print('[ERR] Timed out waiting for device to become ready. Ensure that you have selected the correct device and baud rate.')
            exit(1)
        elif result != b'>':
            print(f'[ERR] Invalid response 0x{result[0]:02X} (expected 0x3E). Ensure that you have selected the correct device and baud rate.')
            exit(1)
    
    print(f'Verifying connection.')
    device.write(b'\t') # send 0x09 to ping device and abort any input in progress
    wait_ready()

    # helper function to read and unpack 32-bit little-endian integers
    def read_32le() -> tuple[int, bytes]:
        data = device.read(4)
        if len(data) != 4:
            print(f'[ERR] Incomplete data read: expected 4 bytes, only got {len(data)} instead')
            exit(1)
        return (struct.unpack('<L', data)[0], data)
    
    # helper function to calculate checksum of one or more byte arrays
    def calculate_checksum(*args: bytes) -> int:
        return (0x100 - (sum([sum(array) for array in args]) & 0xFF)) & 0xFF

    # show device firmware info
    device.write(b'v\n')
    result = device.read_until()[:-1].decode('ascii') # remove newline and decode into string
    print(f'Device firmware information string: {result}')
    wait_ready()

    # list file system if needed
    fs_listing: dict[str, int] = dict() # key = path, value = size
    if ACT_RETRIEVE_FS_LISTING:
        if VERBOSE: # also show file system information
            print(f'[DBG] Retrieving file system information.')
            device.write(b'I\n')
            sz_total, _ = read_32le()
            sz_used, _ = read_32le()
            print(f'[DBG] Device file system stats: total {sz_total} bytes ({sz_used} bytes used)')
            wait_ready()

        print(f'Retrieving file system listing.')
        device.write(b'L\n')
        while True:
            # read entry
            size, _ = read_32le()
            path = device.read_until(b'\0')[:-1].decode('ascii')
            if size == 0xFFFFFFFF and len(path) == 0: break # terminator
            # print(f'{path}:{size}')
            fs_listing[path] = size
        wait_ready()

    if ACT_LIST_FS: # --list-files
        print(f'File system listing ({len(fs_listing)} file(s)):')

        # get length for path and size columns
        path_len = max(max([len(p) for p in fs_listing]) + 1, 4)
        size_len = max(max([len(str(fs_listing[p])) for p in fs_listing]) + 1, 4)

        # print header
        print(f'{'Path': <{path_len}}|{'Size': <{size_len}}')
        print('=' * (path_len + size_len + 1))

        # print entries
        for path in fs_listing:
            print(f'{path: <{path_len}}|{fs_listing[path]: <{size_len}}')
    
    if ACT_DUMP_FS: # --dump-files
        prepare_fs()
        
        print('Dumping files from device.')
        for dev_path in fs_listing: # ACT_DUMP_FS=True -> ACT_RETRIEVE_FS_LISTING=True
            pc_path = os.path.join(FS_PATH, dev_path[1:]) # remove / prefix from dev_path
            print(f' - {dev_path} -> {pc_path}.')
            os.makedirs(os.path.dirname(pc_path), exist_ok=True)
            with open(pc_path, 'wb') as f:
                device.write(f'R:{dev_path}\n'.encode('ascii')) # send command to read file contents
                size, size_bytes = read_32le()
                if size == 0xFFFFFFFF:
                    print(f'[ERR] {dev_path} does not exist - skipping.')
                    wait_ready()
                    continue
                if size == 0xFFFFFFFE or size == 0xFFFFFFFD:
                    print(f'[ERR] {dev_path} cannot be opened - skipping.')
                    wait_ready()
                    continue
                if size != fs_listing[dev_path]:
                    print(f'[WRN] File size changed from {fs_listing[dev_path]} to {size}')
                
                t_start = timer()
                contents = device.read(size) # get contents
                checksum = device.read()[0] # and finally get checksum
                t_end = timer() # time how long it takes to receive data

                if VERBOSE: print(f'[DBG] Retrieved {len(contents)} bytes (expected {size}), checksum: 0x{checksum:02X} (in {(t_end - t_start):.2f} sec)')

                if len(contents) != size:
                    print(f'[ERR] Incomplete data reception: got {len(contents)} bytes, expected {size} - consider changing the timeout duration.')
                    wait_ready()
                    continue

                # verify checksum
                if calculate_checksum(size_bytes, contents) != checksum:
                    print(f'[ERR] Checksum verification failed - discarding.')
                    wait_ready()
                    continue

                f.write(contents)
                wait_ready()
    
    if ACT_UPLOAD: # upload FS to device
        # reformat
        success = True
        if NO_REFORMAT:
            print('Removing all files from file system.')
            device.write(b'X:/\n')
            result = device.read()[0]
            if result == 0x58:
                print('[ERR] Directory deletion failed, aborting.')
                success = False
            elif result != 0x40:
                print(f'[ERR] Unexpected result 0x{result:02X}, aborting.')
                success = False
        else:
            print('Please check the device for notification and confirm reformat request.')
            device.write(b'Z\n')
            
            # implement our own reformat request timeout on top of pyserial's timeout as user confirmation may take that long
            t_start = timer()
            result = b'' # probably unnecessary
            while timer() - t_start < (REFORMAT_REQ_TIMEOUT + DEVICE_TIMEOUT): # allow for extra margin of error
                result = device.read()
                if len(result) != 0: break # we've got something
            
            if len(result) == 0: # timed out for real
                print('[ERR] Timed out waiting for device to transmit user decision.')
                exit(1)
            
            if result[0] == 0x58:
                print('User declined reformat request, or request has been automatically declined after timeout by device.')
                success = False
            elif result[0] != 0x2E:
                print(f'[ERR] Unexpected response 0x{result[0]:02X} for user decision.')
                exit(1)
            else: # user confirmed
                print('Reformatting device - please wait.')
                
                t_start = timer()
                while timer() - t_start < (REFORMAT_TIMEOUT + DEVICE_TIMEOUT):
                    result = device.read()
                    if len(result) != 0: break
                
                if len(result) == 0: # timed out for real
                    print('[ERR] Timed out waiting for device to finish reformatting.')
                    exit(1)
                
                if result[0] == 0x58:
                    print('[ERR] Device reformat failed.')
                    exit(1)
                elif result[0] != 0x40:
                    print(f'[ERR] Unexpected response 0x{result[0]:02X} for reformatting result.')
                    exit(1)
                

        if not success:
            print('Device was not reformatted successfully, exiting.')
            exit(1)
        else:
            wait_ready()
            
            print('Uploading files to device.')

            for (dirpath, dirnames, filenames) in os.walk(FS_PATH):
                for fname in filenames:
                    pc_path = os.path.join(dirpath, fname) # add file name for PC-side path
                    dev_path = pc_path.removeprefix(FS_PATH).replace('\\', '/') # remove FS base path and unify path separator to /

                    print(f' - Uploading {pc_path} -> {dev_path}.')
                    with open(pc_path, 'rb') as f:
                        device.write(f'W:{dev_path}\n'.encode('ascii')) # step 1
                        result = device.read(5)
                        if len(result) == 0:
                            print(f'[ERR] Timed out waiting for device to create/open file {dev_path}.')
                            exit(1)

                        if result[0] == 0x58:
                            print(f'[ERR] Device cannot create/open file {dev_path} - skipping.')
                        elif result[0] != 0x40:
                            print(f'[ERR] Unexpected response 0x{result[0]:02X} for file write command.')
                            exit(1)
                        else:
                            while True: # send data blocks
                                block = f.read(256) # read up to 256 bytes
                                if len(block) == 0:
                                    if VERBOSE: print(f'[DBG] File transmission complete.')
                                    break

                                while True:
                                    device.write(b'\x42')
                                    block_sz = bytes([len(block) - 1]) # block size - 1
                                    device.write(block_sz)
                                    device.write(block)
                                    checksum = calculate_checksum(b'\x42', block_sz, block) # block checksum
                                    device.write(bytes([checksum]))
                                    
                                    result = device.read()
                                    if len(result) == 0:
                                        print(f'[ERR] Timed out waiting for device to write.')
                                        exit(1)
                                    result = result[0] # unpack
                                    offset, _ = read_32le()
                                    # if VERBOSE: print(f'[DBG] Result code: 0x{result:02X}, file offset: 0x{offset:08X}')
                                    if result == 0x21:
                                        print(f'[ERR] Checksum validation failure - resending data.')
                                        continue
                                    elif result == 0x58:
                                        print(f'[ERR] Unable to write data to file {dev_path} (stuck at offset {offset}).')
                                        exit(1)
                                    elif result != 0x40:
                                        print(f'[ERR] Unexpected response 0x{result:02X} to data block writing.')
                                        exit(1)
                                    else: # success
                                        if VERBOSE: print(f'[DBG] Successfully written {len(block)} bytes to {dev_path} (new offset: {offset}).')
                                        break
                                
                            if VERBOSE: print(f'[DBG] Terminating file writing operation.')
                            device.write(b'\x46')
                            wait_ready()

    print(f'Closing serial communication.')
    device.close()