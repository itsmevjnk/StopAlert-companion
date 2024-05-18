import os
import csv
import datetime
import itertools
import json

class Trip:
    # constructor
    def __init__(self, id: str, headsign: str, direction: bool):
        self.id = id
        self.headsign = headsign
        self.direction = direction
    
    # convert trip to string
    def __str__(self) -> str:
        return f'(\'{self.id}\': (\'{self.headsign}\', {self.direction}))'
    
    # convert trip to representative string
    def __repr__(self) -> str:
        return f'Trip(\'{self.id}\',\'{self.headsign}\',{self.direction})'

class Coordinates:
    # constructor
    def __init__(self, lat: float, lon: float):
        self.latitude = lat
        self.longitude = lon
    
    # convert coordinate to string
    def __str__(self) -> str:
        return f'({self.latitude},{self.longitude})'

    # convert coordinate to representative string
    def __repr__(self) -> str:
        return f'Coordinates({self.latitude},{self.longitude})'

class Stop:
    # constructor
    def __init__(self, id: str, name: str, coords: Coordinates):
        self.id = id
        self.name = name
        self.coords = coords
    
    def __init__(self, id: str, name: str, lat: float, lon: float):
        self.id = id
        self.name = name
        self.coords = Coordinates(lat, lon)
    
    # convert stop to string
    def __str__(self) -> str:
        return f'\'{self.id}\': {self.name} {self.coords}'
    
    # convert stop to representative string
    def __repr__(self) -> str:
        return f'Stop(\'{self.id}\',\'{self.name}\',{self.coords})'

class GTFS:
    # constructor
    def __init__(self, basedir: str):
        self.basedir = basedir # save base directory

    # get list of routes
    # returns a dictionary with key being route number (str), value being tuple of route name and list of its route IDs
    def get_routes(self, fname: str = 'routes.txt') -> dict[str, tuple[str, list[str]]]:
        routes: dict[str, tuple[str, list[str]]] = dict() # create result list
        with open(os.path.join(self.basedir, fname), 'r', encoding='utf-8-sig') as f: # open routes file for parsing
            reader = csv.DictReader(f)
            for row in reader: # go through each row
                route_num = row['route_short_name']
                if routes.get(route_num) is None: # route hasn't been recorded yet
                    route_name = row['route_long_name']
                    routes[route_num] = (route_name, [])
                # print(row)
                routes[route_num][1].append(row['route_id']) # append route ID to list
        return routes
    
    # get list of trips
    # ids can be either a list of route IDs, or a dictionary mapping route number to route IDs, or it can be None (in which case the entire list is read and sorted by route ID)
    # will return accordingly: the 1st and 3rd cases return a dictionary mapping route IDs to lists of trip info, the 2nd case returns dictionary mapping route number to dictionary mapping route ID to lists of trip info
    # trip info is an object of Trip class (above)
    def get_trips(self, ids: list[str] | dict[str, list[str]] | None = None, fname: str = 'trips.txt') \
        -> dict[str, list[Trip]] | dict[str, dict[str, list[Trip]]]:
        # create output list (unformatted, i.e. only mapping route ID to trips)
        if isinstance(ids, list): trips: dict[str, list[Trip]] = {id: [] for id in ids}
        elif isinstance(ids, dict):
            trips: dict[str, list[Trip]] = {id: [] for route_num in ids for id in ids[route_num]}
            route_ids: dict[str, str] = {route_id: route_num for route_num in ids for route_id in ids[route_num]} # also create mapping of route ID to route number
        else: trips: dict[str, list[Trip]] = dict()

        with open(os.path.join(self.basedir, fname), 'r', encoding='utf-8-sig') as f: # open trips file for parsing
            reader = csv.DictReader(f)
            for row in reader: # go through each row
                # extract key data
                trip_id = row['trip_id']
                route_id = row['route_id']
                headsign = row['trip_headsign']
                direction = (int(row['direction_id']) != 0)

                # add trip to our dict
                if ids is None and route_id not in trips: trips[route_id] = [] # we don't have list of route ID to base trips on, so we'll figure out which route ID to add in as we go
                if route_id in trips: trips[route_id].append(Trip(trip_id, headsign, direction))
        
        if isinstance(ids, dict): # group route IDs into route numbers
            trips_grouped: dict[str, dict[str, list[Trip]]] = {route_num: dict() for route_num in ids}
            for route_id in trips:
                route_num = route_ids[route_id]
                trips_grouped[route_num][route_id] = trips[route_id] # add trips list to grouped dict
            return trips_grouped
        else: return trips # no need to group

    # get list of stops
    # returns a dictionary mapping stop ID to stop information (which also contains the stop ID)
    def get_stops(self, fname: str = 'stops.txt') -> dict[str, Stop]:
        stops: dict[str, Stop] = dict() # create dictionary for storing stops
        with open(os.path.join(self.basedir, fname), 'r', encoding='utf-8-sig') as f: # open stops file for parsing
            reader = csv.DictReader(f)
            for row in reader: # go through each row
                id = row['stop_id']
                stops[id] = Stop(id, row['stop_name'], float(row['stop_lat']), float(row['stop_lon']))
        return stops
    
    # get stopping pattern of the specified trip(s)
    # returns a list of tuples of arrival time and stop ID (or only stop ID if get_time=False) in the stopping sequence order
    def get_trip_stop_pattern(self, ids: str | list[str], fname: str = 'stop_times.txt', get_time=True) \
        -> list[tuple[datetime.time, str] | str] | dict[str, list[tuple[datetime.time, str] | str]]:
        trip_ids = ids if isinstance(ids, list) else [ids] # normalise trip IDs to list
        sequence: dict[str, dict[int, tuple[datetime.time, str] | str]] = {id: dict() for id in trip_ids} # stopping sequence with sequence index as key

        # lines = 0
        with open(os.path.join(self.basedir, fname), 'r', encoding='utf-8-sig') as f: # open stop times file for parsing
            reader = csv.DictReader(f)
            for row in reader: # go through each row
                trip_id = row['trip_id']
                arrival_time = row['arrival_time'] # only needed if get_time=True
                stop_id = row['stop_id']
                seq = int(row['stop_sequence'])

                if trip_id in sequence: # we want this entry
                    sequence[trip_id][seq] = (datetime.datetime.strptime(arrival_time, '%H:%M:%S').time(), stop_id) if get_time else stop_id
                
                # lines += 1
        # print(f'Read {lines} lines')
        
        # sort stopping sequences
        sequence_sorted: dict[str, list[tuple[datetime.time, str] | str]] = {
            trip_id: [item[1] for item in sorted(sequence[trip_id].items())]
            for trip_id in sequence
        }

        if isinstance(ids, list): return sequence_sorted # return as is
        else: return sequence_sorted[ids] # unpack the only item in there
    
    # get stopping pattern of specified route(s) by merging stopping patterns of all of its trips in the specified direction (or both directions if direction=None)
    # resolve_direction_name can be set to True to resolve the headsign name
    def get_route_stop_pattern(self, route_nums: str | list[str], direction: bool | None = None, resolve_direction_name: bool = False, fname_routes: str = 'routes.txt', fname_trips: str = 'trips.txt', fname_stop_times: str = 'stop_times.txt') \
        -> list[str] | dict[bool | str, list[str]] | dict[str, list[str]] | dict[str, dict[bool | str, list[str]]]:
        routes = route_nums if isinstance(route_nums, list) else [route_nums] # normalise route numbers list

        # extract route IDs of interest
        all_routes = self.get_routes(fname_routes) # get all routes
        route_ids: dict[str, list[str]] = {num: all_routes[num][1] for num in routes}

        # extract trips of interest
        trips: dict[str, dict[str, list[Trip]]] = self.get_trips(route_ids, fname_trips) # get all trips grouped by route number and ID
        trips: dict[str, list[Trip]] = {num: list(itertools.chain.from_iterable(trips[num].values())) for num in trips} # merge all route IDs for a route number together
        trips_false: dict[str, list[str]] = {num: list() for num in trips}; trips_true: dict[str, list[str]] = {num: list() for num in trips} # create list of trip IDs with direction=False and direction=True
        route_direction_name: dict[str, dict[bool, set]] = {num: {True: set(), False: set()} for num in trips} # route direction names (to be used if resolve_direction_name=True)
        for num in trips:
            for trip in trips[num]:
                if trip.direction: trips_true[num].append(trip.id)
                else: trips_false[num].append(trip.id)
                route_direction_name[num][trip.direction].add(trip.headsign) # add headsign to set of direction names
        route_direction_name: dict[str, dict[bool, str]] = {num: {dir: '/'.join(route_direction_name[num][dir]) for dir in [True, False]} for num in route_direction_name} # convert from set to str

        # prepare list of trips to be fetched
        if direction is None:
            fetch_trips = list(itertools.chain.from_iterable(trips_false.values())) + list(itertools.chain.from_iterable(trips_true.values()))
        else:
            fetch_trips = list(itertools.chain.from_iterable(trips_true.values() if direction else trips_false.values()))
        #print(fetch_trips)

        # fetch stopping patterns
        patterns = self.get_trip_stop_pattern(fetch_trips, fname_stop_times, False) # key = trip ID, value = stop IDs in sequence
        # with open('patterns.json', 'w') as f: json.dump(patterns, f, indent=4)
        # with open('patterns.json', 'r') as f: patterns = json.load(f)

        # merge stopping patterns of trips
        def merge_patterns(trip_ids: list[str]) -> list[str]:
            def merge_two_patterns(a: list[str], b: list[str]) -> list[str] | None: # helper method to merge two patterns
                # check if there is a common element among two of them that we can lock onto
                common_elem = set(a).intersection(b)
                if len(common_elem) == 0: return None # no common elements, so the lists are too distinct to be merged

                # get indices of common elements
                common_idx_a = [a.index(elem) for elem in common_elem]
                common_idx_b = [b.index(elem) for elem in common_elem]
                # print(common_idx_a); print(common_idx_b)

                # get common element index range
                common_idx_a_min = min(common_idx_a); common_idx_a_max = max(common_idx_a)
                common_idx_b_min = min(common_idx_b); common_idx_b_max = max(common_idx_b)
                if common_idx_a_min != 0 and common_idx_b_min != 0: raise ValueError() # assumption to simplify our lives
                
                # swap a and b so that b comes after a
                if common_idx_b_min != 0:
                    t = common_idx_a_min; common_idx_a_min = common_idx_b_min; common_idx_b_min = t
                    t = common_idx_a_max; common_idx_a_max = common_idx_b_max; common_idx_b_max = t
                    t = a; a = b; b = t
                
                # get overlap of a and b
                a_overlap = a[common_idx_a_min:common_idx_a_max + 1]
                b_overlap = b[common_idx_b_min:common_idx_b_max + 1]
                # print(a_overlap); print(b_overlap)

                # iterate through overlap region
                overlap = [] # merged overlap
                i = 0; j = 0 # indices of a_overlap and b_overlap
                while i < len(a_overlap) and j < len(b_overlap):
                    a_i = a_overlap[i] if i < len(a_overlap) else None
                    b_j = b_overlap[j] if j < len(b_overlap) else None
                    if a_i is not None and b_j is not None and a_i == b_j:
                        overlap.append(a_i)
                        i += 1; j += 1
                    elif i < len(a_overlap) - 1 and a_overlap[i + 1] == b_j: # e.g. 28434,10443,28436 and 28434,28436
                        overlap.append(a_i)
                        i += 1
                    elif j < len(b_overlap) - 1 and b_overlap[j + 1] == a_i: # e.g. 28434,28436 and 28434,10443,28436
                        overlap.append(b_j)
                        j += 1
                    else: return None # different route altogether
                
                # print(a[:common_idx_a_min])
                # print(overlap)
                # print(b[common_idx_b_max + 1:])
                
                return a[:common_idx_a_min] + overlap + (b[common_idx_b_max + 1:] if common_idx_b_max != len(b) - 1 else a[common_idx_a_max + 1:]) # b can be a subsequence of a too
            
            pattern_queue = [patterns[trip_id] for trip_id in trip_ids] # queue of patterns to be merged
            result = pattern_queue.pop() # get a random pattern to get started
            while len(pattern_queue) != 0: # keep merging until there aren't any more to merge
                merged = False
                for i in range(len(pattern_queue)):
                    try: result_new = merge_two_patterns(result, pattern_queue[i]) # attempt to merge
                    except ValueError: result_new = None
                    if result_new is not None: # merge successful
                        merged = True
                        result = result_new
                        pattern_queue.pop(i) # remove from queue
                        break
                
                if not merged:
                    # raise ValueError(f'There are {len(pattern_queue)} pattern(s) that cannot be merged')
                    break
            return result # all good
        
        result = dict() # our output
        for route_num in routes:
            if direction is None: # both directions
                result[route_num] = {
                    route_direction_name[route_num][True] if resolve_direction_name else True: merge_patterns(trips_true[route_num]),
                    route_direction_name[route_num][False] if resolve_direction_name else False: merge_patterns(trips_false[route_num])
                }
            elif resolve_direction_name: # one direction only, with direction name resolution
                result[route_num] = {
                    route_direction_name[route_num][direction]: merge_patterns(trips_true[route_num] if direction else trips_false[route_num])
                }
            else: # one direction only, with no direction name resolution
                result[route_num] = merge_patterns(trips_true[route_num] if direction else trips_false[route_num])
        
        if isinstance(route_nums, list): return result # route_nums was given as a list - no unpacking necessary
        else: return result[route_nums] # unpack only route
