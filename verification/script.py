#!/usr/bin/env python3

# Inspired by: https://github.com/leonardobarilani/warehouse-model-checking/blob/main/src/simulation/sim.py

import argparse
import csv
from itertools import product
import json
import os
from multiprocessing import Pool
import re
import shutil
import signal
import subprocess
from time import time
from tqdm import tqdm
import xml.etree.ElementTree as ET

def handle_sigint(*_):
    exit(0)

def get_args():
    ap = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('-v', '--verifyta', type=str, metavar='VERIFYTA_PATH', default='/Applications/UPPAAL.app/Contents/Resources/uppaal/bin/verifyta', help='path to verifyta executable')
    ap.add_argument('-s', '--scenario', type=str, metavar='SCENARIO', default='extensive', help='name of the scenario to run, otherwise an extensive search is performed')
    ap.add_argument('-nq', '--no-queries', default=False, action=argparse.BooleanOptionalAction)
    ap.add_argument('-np', '--no-probabilities', default=False, action=argparse.BooleanOptionalAction)
    ap.add_argument('-ns', '--no-simulations', default=False, action=argparse.BooleanOptionalAction)
    ap.add_argument('--short', default=False, action=argparse.BooleanOptionalAction)
    ap.add_argument('config_fname', type=str, metavar='config.json', help='configuration to use for the simulation')
    ap.add_argument('project_fname', type=str, metavar='project.xml', help='project template to use for simulation')
    return ap.parse_args()

def get_project(file_name: str) -> list[str]:
    project = []
    with open(file_name, 'r') as file:
        for line in file.readlines():
            project.append(line[:-1])
    return project

def get_config(file_name: str) -> dict:
    with open(file_name, 'r') as file:
        return json.loads(file.read())

def parse_project(content: str, start: str) -> list[str]:
    tree = ET.fromstring(content)
    result = []
    for formula in tree.iter('formula'):
        if not formula.text == None and formula.text.startswith(start):
            result.append(formula.text)
    return [item.replace('\n', ' ').replace('\t', '') for item in result]

def get_queries(content: str) -> list[str]:
    return parse_project(content, 'A')

def get_probabilities(content: str) -> list[str]:
    return parse_project(content, 'Pr')

def get_simulations(content: str) -> list[str]:
    return parse_project(content, 'simulate')

def generate_properties(properties: list[str], path: str, prefix: str):
    index = 0
    for property in properties:
        with open(os.path.join(path, '{}_{:02d}.txt'.format(prefix, index)), 'w') as file:
            file.write('{}\n'.format(property))
        index += 1

def generate_queries(queries: list[str], path: str):
    generate_properties(queries, path, 'query')

def generate_probabilities(probabilities: list[str], path: str):
    generate_properties(probabilities, path, 'probability')

def generate_simulations(simulations: list[str], path: str):
    generate_properties(simulations, path, 'simulation')

def to_array(values: list[int], short: bool = False):
    if not short:
        return '{{{}}}'.format(', '.join([str(value) for value in values]))
    else:
        return '[{}]'.format(','.join([str(value) for value in values]))

def get_space_length(max: list[int], min: list[int]) -> int:
    value = 1
    for index in range(0, len(max)):
        value *= (max[index] + 1 - min[index])
    return value

def get_extensive_length(values: list[dict]):
    return (int(values['speed']['max']) + 1 - int(values['speed']['min'])) * \
           (int(values['disks']['max']) + 1 - int(values['disks']['min'])) * \
           (int(values['policy']['max']) + 1 - int(values['policy']['min'])) * \
           get_space_length(values['out_sensors']['max'], values['out_sensors']['min']) * \
           get_space_length(values['stations_processing']['max'], values['stations_processing']['min'])

def get_space(min: list[int], max: list[int]):
    ranges = []
    for index in range(0, len(min)):
        ranges.append(range(min[index], max[index] + 1))
    for result in product(*ranges):
        yield result

def generate_extensive_project(values: list[dict]):
    for speed in range(values['speed']['min'], values['speed']['max'] + 1):
        for disks in range(values['disks']['min'], values['disks']['max'] + 1):
            for policy in range(values['policy']['min'], values['policy']['max'] + 1):
                for sensors in get_space(values['out_sensors']['min'], values['out_sensors']['max']):
                    for stations in get_space(values['stations_processing']['min'], values['stations_processing']['max']):
                        yield {
                            'speed': speed,
                            'disks': disks,
                            'policy': policy,
                            'out_sensors': sensors,
                            'stations_processing': stations
                        }

def generate_project(project: list[str], values: list[dict], name: str):
    new_project = []
    system = False
    done = False
    static = '''
const SlotId POS_IN_SENSORS_IN_ORDER[STATIONS] = {POS_IN_SENSORS[0], POS_IN_SENSORS[1], POS_IN_SENSORS[3], POS_IN_SENSORS[2], POS_IN_SENSORS[4], POS_IN_SENSORS[5]};
const OutSensorId OUT_SENSORS_ID_IN_ORDER[STATIONS] = {1, 2, 4, 3, 4, 0};
const StationId IN_SENSORS_STATION[IN_SENSORS] = {0, 1, 3, 2, 4, 5};

initializer = Initializer(DISKS);
motor = Motor(SPEED);
conveyorBelt = ConveyorBelt();
station(const StationId id) = Station(id, POS_STATIONS[id], STATIONS_ELABORATION_TIME[id], POS_IN_SENSORS_IN_ORDER[id], OUT_SENSORS_ID_IN_ORDER[id]);
inSensor(const InSensorId id) = InSensor(id, IN_SENSORS_STATION[id]);
outSensor(const OutSensorId id) = OutSensor(id, POS_OUT_SENSORS[id]);
'''
    for line in project:
        if not system and '<system>' not in line:
            new_project.append('{}\n'.format(line))
        elif '</system>' in line:
            system = False
        elif not done:
            system = True
            done = True
            new_project.append('    <system>\n')
            new_project.append('const int SPEED = {};\n'.format(values['speed']))
            new_project.append('const int[1, 12] DISKS = {};\n'.format(values['disks']))
            new_project.append('const SlotId POS_OUT_SENSORS[OUT_SENSORS] = {};\n'.format(to_array(values['out_sensors'])))
            new_project.append('const int STATIONS_ELABORATION_TIME[STATIONS] = {};\n'.format(to_array(values['stations_processing'])))
            new_project.append(static)
            if values['policy'] == 0:
                new_project.append('flowController = FlowController_0(POS_OUT_SENSORS[2], POS_OUT_SENSORS[3]);\n')
            else:
                new_project.append('flowController = FlowController_{}();\n'.format(values['policy']))
            new_project.append('system initializer, motor, conveyorBelt, station, inSensor, outSensor, flowController;\n')
            new_project.append('    </system>\n')
    with open(name, 'w') as file:
        file.writelines(new_project)

def generate_projects(config: dict, scenario: str):
    if scenario == 'extensive':
        return generate_extensive_project(config[scenario]), get_extensive_length(config[scenario])
    elif scenario in config:
        return [{
                'speed': config[scenario]['speed'],
                'disks': config[scenario]['disks'],
                'policy': config[scenario]['policy'],
                'out_sensors': config[scenario]['out_sensors'],
                'stations_processing': config[scenario]['stations_processing']
            }], 1
    else:
        print('Configuration not found')
        return (None, 0)

def output_folder_parser(path: str, prefix: str) -> list[list[str]]:
    items = []
    for item in os.listdir(path):
        if item.startswith(prefix):
            items.append(os.path.join(path, item))
    return items

def output_folder_queries(path: str) -> list[list[str]]:
    return output_folder_parser(path, 'query')

def output_folder_probabilities(path: str) -> list[list[str]]:
    return output_folder_parser(path, 'probability')

def output_folder_simulations(path: str) -> list[list[str]]:
    return output_folder_parser(path, 'simulation')

def gen_args(content: list[str], verifier: str, projects: list, properties: list[str], path: str):
    for project in projects:
        for property in properties:
            yield content, verifier, project, property, path

def gen_name(values: dict, property: str):
    return 's{}-d{}-p{}-os{}-sp{}-{}'.format(
        values['speed'],
        values['disks'],
        values['policy'],
        to_array(values['out_sensors'], short=True),
        to_array(values['stations_processing'], short=True),
        property[-6:-4])

def run_property(parameters: list[tuple[list[str], str, dict, str, str]]) -> bool:
    start = time()
    content, verifier, project, property, path = parameters
    name = gen_name(project, property)
    fullname = os.path.join(path, 'project_{}.xml'.format(name))
    generate_project(content, project, fullname)
    result = subprocess.run([verifier, fullname, property], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    os.remove(fullname)
    if result.returncode != 0:
        print('[ERROR]', result.stderr.decode().rstrip())
        exit(1)
    return result.stdout.decode(), name, property.split('/')[-1], time() - start

def run_all(content: list[str], verifier: str, projects: list, properties: list[str], path: str, prefix: str, description: str, length: int) -> dict:
    pool = Pool(os.cpu_count() - 1)
    results = {}
    print('\033[;1m')
    for result, project, property, time in tqdm(pool.imap_unordered(run_property, gen_args(content, verifier, projects, properties, path)), desc=description, total=length * len(properties)):
        property = property.split('{}_'.format(prefix))[1].strip('.txt')
        results[(project[:-3], property)] = (result, '{0:.2f} seconds'.format(time))
    pool.close()
    pool.join()
    print('\033[0m')
    return results

def run_all_queries(content: list[str], verifier: str, projects: list, queries: list[str], path: str, length: str) -> dict:
    return run_all(content, verifier, projects, queries, path, 'query', 'Verifying queries', length)

def run_all_probabilities(content: list[str], verifier: str, projects: list, probabilities: list[str], path: str, length: str) -> dict:
    return run_all(content, verifier, projects, probabilities, path, 'probability', 'Calculating probabilities', length)

def run_all_simulations(content: list[str], verifier: str, projects: list, simulations: list[str], path: str, length: str) -> dict:
    return run_all(content, verifier, projects, simulations, path, 'simulation', 'Simulating', length)

def print_queries(results: dict, queries: str, verbose: bool):
    projects = {}
    failed = False
    for project, query in results:
        if project not in projects:
            projects[project] = {}
        result, time = results[(project, query)]
        projects[project][query] = {'query': queries[int(query)], 'result': 'Formula is satisfied' in result, 'time': time}
        if not result:
            failed = True
    for project in projects:
        projects[project] = dict(sorted(projects[project].items()))
    projects = dict(sorted(projects.items()))
    if failed:
        print('\033[31;1mSome properties aren\'t satisfied!\033[0m\n')
    else:
        print('\033[32;1mAll the properties are satisfied!\033[0m\n')
    if verbose:
        print('\033[;1mVerification results\033[0m:')
        print(json.dumps(projects))
        print()

def print_probabilities(results: dict, probabilities: str, verbose: bool):
    projects = {}
    interval_regex = re.compile(r'\[([\d.e-]+),([\d.e-]+)\]\s+\(([\d]+)\% CI\)')
    values_regex = re.compile(r'Values in \[(\d+),(\d+)\] mean=(\d+) steps=1: (.+)')
    for project, probability in results:
        if project not in projects:
            projects[project] = {}
        result, time = results[(project, probability)]
        if 'Formula is satisfied' in result:
            matches = interval_regex.findall(result.split('\r\n')[-3])[0]
            confidence = float(matches[2]) / 100
            interval = {'min': float(matches[0]), 'max': float(matches[1])}
            matches = values_regex.findall(result.split('\r\n')[-2])[0]
            values_range = {'min': int(matches[0]), 'max': int(matches[1])}
            mean = float(matches[2])
            values = [int(match) for match in matches[3].split(' ')]
        else:
            interval = {'min': 0.0, 'max': 0.0}
            confidence = 0.0
            values_range = {'min': 0, 'max': 0}
            mean = 0.0
            values = []
        projects[project][probability] = {'probability': probabilities[int(probability)], 'result': {'outcome': 'Formula is satisfied' in result, 'interval': interval, 'confidence': confidence, 'values': {'range': values_range, 'mean': mean, 'samples': values}}, 'time': time}
    for project in projects:
        projects[project] = dict(sorted(projects[project].items()))
    projects = dict(sorted(projects.items()))
    if verbose:
        print('\033[;1mProbabilities\033[0m:')
        print(json.dumps(projects))
        print()

def process_values(result: str, index: int, path: str) -> tuple[str, int]:
    get_values = re.compile(r'\(([\d.]+),(\d+)\)')
    results = result.split('Verifying formula')[1].split('\r\n')[2:-1]
    result = {}
    while len(results) > 0:
        index += 1
        formula = results[0]
        values = [[int(value[0].split('.')[0]), int(value[1])] for value in get_values.findall(results[1])]
        results = results[2:]
        file_name = '{}_{:02d}.csv'.format('values', index)
        with open(os.path.join(path, file_name), 'w') as file:
            writer = csv.writer(file, delimiter=',', lineterminator='\n')
            writer.writerow(['x', 'y'])
            writer.writerows(values)
            file.close()
        result[formula] = file_name
    return result, index

def print_simulations(results: dict, simulations: str, path: str, verbose: bool):
    projects = {}
    index = 0
    for project, simulation in results:
        if project not in projects:
            projects[project] = {}
        result, time = results[(project, simulation)]
        values, index = process_values(result, index, path)
        projects[project][simulation] = {'simulation': simulations[int(simulation)], 'result': 'Formula is satisfied' in result, 'series': values, 'time': time}
    for project in projects:
        projects[project] = dict(sorted(projects[project].items()))
    projects = dict(sorted(projects.items()))
    if verbose:
        print('\033[;1mSimulations\033[0m:')
        print(json.dumps(projects))
        print()

if __name__ == '__main__':

    output_directory = 'tmp'
    result_directory = 'results'

    signal.signal(signal.SIGINT, handle_sigint)

    if os.path.isdir(output_directory):
        shutil.rmtree(output_directory)
    os.makedirs(output_directory)

    if os.path.isdir(result_directory):
        shutil.rmtree(result_directory)
    os.makedirs(result_directory)

    args = get_args()

    if not os.path.isfile(args.verifyta):
        print(f'[ERROR] verifyta executable not found at "{args.verifyta}"')
        print('[ERROR] Please provide a valid path with --verifyta PATH')
        exit(1)

    if not os.path.isfile(args.config_fname):
        print(f'[ERROR] Configuration file not found at "{args.config_fname}"')
        print('[ERROR] Please provide a valid path')
        exit(1)

    if not os.path.isfile(args.project_fname):
        print(f'[ERROR] Template file not found at "{args.project_fname}"')
        print('[ERROR] Please provide a valid path')
        exit(1)

    project = get_project(args.project_fname)
    config = get_config(args.config_fname)
    full_project = '\n'.join(project)

    queries = get_queries(full_project)
    probabilities = get_probabilities(full_project)
    simulations = get_simulations(full_project)

    generate_queries(queries, output_directory)
    generate_probabilities(probabilities, output_directory)
    generate_simulations(simulations, output_directory)
    projects, length = generate_projects(config, args.scenario)

    if projects is not None:
        if not args.no_queries and len(queries) > 0:
            print_queries(run_all_queries(project, args.verifyta, projects, output_folder_queries(output_directory), output_directory, length), queries, not args.short)
        if not args.no_probabilities and len(probabilities) > 0:
            print_probabilities(run_all_probabilities(project, args.verifyta, projects, output_folder_probabilities(output_directory), output_directory, length), probabilities, not args.short)
        if not args.no_simulations and len(simulations) > 0:
            print_simulations(run_all_simulations(project, args.verifyta, projects, output_folder_simulations(output_directory), output_directory, length), simulations, result_directory, not args.short)

    shutil.rmtree(output_directory)
    if len(os.listdir(result_directory)) == 0:
        shutil.rmtree(result_directory)
