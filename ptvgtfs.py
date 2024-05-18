import os
import requests
from io import BytesIO
from zipfile import ZipFile
from pathlib import Path

# list of transport modes (from DTP GTFS release notes)
_modes = {
    'RegionalTrain': 1,
    'MetroTrain': 2,
    'MetroTram': 3,
    'MetroBus': 4,
    'RegionalCoach': 5,
    'RegionalBus': 6,
    'TeleBus': 7, # dead
    'NightBus': 8, # dead
    'Interstate': 10, # interstate trains
    'SkyBus': 11
}

# download the GTFS zip file and return its content
def download_zip():
    # get dataset from PTV
    print('Downloading GTFS datasets from PTV...', end='')
    resp = requests.get('https://data.ptv.vic.gov.au/downloads/gtfs.zip')
    if resp.status_code != 200:
        raise ConnectionError(f'Unexpected status code {resp.status_code} from PTV server')
    print('done.')
    return resp.content

# download the GTFS zip file and extract it into zip files for various transport modes
def download_files(modes: str | list[str] = _modes.keys(), path: str = os.getcwd()):    
    # extract datasets for each mode
    datasets_zip = ZipFile(BytesIO(download_zip()))
    def extract_single(mode: str):
        id = _modes.get(mode)
        if id is None:
            raise ValueError(f'Invalid mode {mode}')
        
        print(f'Extracting {mode} (ID {id})...', end='')
        with open(os.path.join(path, f'{mode}.zip'), 'wb') as f:
            f.write(datasets_zip.read(f'{id}/google_transit.zip'))
        print('done.')
    
    if modes is str: # there's a single mode to work with
        extract_single(modes)
    else: # there's multiple modes
        for mode in modes:
            extract_single(mode)

# download the GTFS zip file and return the zip file buffers for the specified modes (specified by name, see _modes above)
def download_bufs(modes: str | list[str] = _modes.keys()) -> bytes | dict[str, bytes]:
    # extract datasets for each mode
    datasets_zip = ZipFile(BytesIO(download_zip()))
    def extract_single(mode: str):
        id = _modes.get(mode)
        if id is None:
            raise ValueError(f'Invalid mode {mode}')
        
        print(f'Extracting {mode} (ID {id})...', end='')
        result = datasets_zip.read(f'{id}/google_transit.zip')
        print('done.')
        return result
    
    if modes is str: # there's a single mode to work with
        return extract_single(modes)
    else: # there's multiple modes
        return {mode: extract_single(mode) for mode in modes}

# download the GTFS zip file and extract datasets into their respective directories for the specified modes (specified by name, see _modes above) - e.g. MetroBus/*.txt
# NOTE: due to the datasets' large sizes, there won't be an option to extract them into memory like download_bufs!
def download_datasets(modes: str | list[str] = _modes.keys(), path: str = os.getcwd()):
    if type(modes) == str: modes = [modes] # normalise to list
    zip_bufs = download_bufs(modes) # download datasets' zip files
    for mode in zip_bufs: # extract each dataset
        out_path = os.path.join(path, mode)
        print(f'Extracting {mode} dataset to {out_path}...', end='')
        Path(out_path).mkdir(parents=True, exist_ok=True) # create directory if it doesn't exist
        dataset_zip = ZipFile(BytesIO(zip_bufs[mode])) # open dataset
        dataset_zip.extractall(out_path) # and extract it to its own directory
        print('done.')
    

