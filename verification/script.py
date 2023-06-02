#!/usr/bin/env python3

# Inspired by: https://github.com/leonardobarilani/warehouse-model-checking/blob/main/src/simulation/sim.py

import argparse
import csv
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
    ap.add_argument('-s', '--scenario', type=str, metavar='SCENARIO', default='all', help='name of the scenario to run ("all" for running them all)')
    ap.add_argument('-nq', '--no-queries', default=False, action=argparse.BooleanOptionalAction)
    ap.add_argument('-np', '--no-probabilities', default=False, action=argparse.BooleanOptionalAction)
    ap.add_argument('-ns', '--no-simulations', default=False, action=argparse.BooleanOptionalAction)
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
    return result

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

def get_line(line: str, change: dict):
    if type(change['value']) is list:
        return '{}= {{{}}};\n'.format(line.split('=')[0], ', '.join([str(item) for item in change['value']]))
    else:
        return '{}= {};\n'.format(line.split('=')[0], change['value'])

def generate_project(project: list[str], changes: list[dict], path: str, name: str):
    new_project = []
    for line in project:
        changed = False
        for change in changes:
            if line.startswith('const int') and change['param'] in line.split(';')[0]:
                new_project.append(get_line(line, change))
                changed = True
                break
        if not changed:
            new_project.append('{}\n'.format(line))
    with open(os.path.join(path,'project_{}.xml'.format(name)), 'w') as file:
        file.writelines(new_project)

def generate_projects(project: list[str], config: dict, scenario: str, path: str) -> bool:
    if scenario == 'all':
        for key, value in config.items():
            generate_project(project, value, path, key)
        return True
    elif scenario in config:
        generate_project(project, config[scenario], path, scenario)
        return True
    else:
        print('Configuration not found')
        return False

def output_folder_parser(path: str, prefix: str) -> list[list[str]]:
    projects = []
    items = []
    for item in os.listdir(path):
        if item.startswith('project'):
            projects.append(os.path.join(path, item))
        elif item.startswith(prefix):
            items.append(os.path.join(path, item))
    return projects, items

def output_folder_queries(path: str) -> list[list[str]]:
    return output_folder_parser(path, 'query')

def output_folder_probabilities(path: str) -> list[list[str]]:
    return output_folder_parser(path, 'probability')

def output_folder_simulations(path: str) -> list[list[str]]:
    return output_folder_parser(path, 'simulation')

def run_property(parameters: list[tuple[str, str, str]]) -> bool:
    start = time()
    verifier, project, property = parameters
    result = subprocess.run([verifier, '-C', '-H', '32', '-S', '2', '-w', '1', project, property], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print('[ERROR]', result.stderr.decode().rstrip())
        exit(1)
    return result.stdout.decode(), project.split('/')[-1], property.split('/')[-1], time() - start

def run_all(verifier: str, projects: list[str], properties: list[str], prefix: str, description: str) -> dict:
    pool = Pool(os.cpu_count() - 1)
    args = [(verifier, project, property) for project in projects for property in properties]
    results = {}
    print('\033[;1m')
    for result, project, property, time in tqdm(pool.imap_unordered(run_property, args), desc=description, total=len(args)):
        project = project.split('project_')[1].strip('.xml')
        property = property.split('{}_'.format(prefix))[1].strip('.txt')
        results[(project, property)] = (result, '{0:.2f} seconds'.format(time))
    pool.close()
    pool.join()
    print('\033[0m')
    return results

def run_all_queries(verifier: str, projects: list[str], queries: list[str]) -> dict:
    return run_all(verifier, projects, queries, 'query', 'Verifying queries')

def run_all_probabilities(verifier: str, projects: list[str], probabilities: list[str]) -> dict:
    return run_all(verifier, projects, probabilities, 'probability', 'Calculating probabilities')

def run_all_simulations(verifier: str, projects: list[str], simulations: list[str]) -> dict:
    return run_all(verifier, projects, simulations, 'simulation', 'Simulating')

def print_queries(results: dict, queries: str):
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
    print('\033[;1mVerification results\033[0m:')
    print(json.dumps(projects))
    print()

def print_probabilities(results: dict, probabilities: str):
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

def print_simulations(results: dict, simulations: str, path: str):
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
    simulations = [simulation.replace('\n', ' ').replace('\t', '') for simulation in get_simulations(full_project)]

    generate_queries(queries, output_directory)
    generate_probabilities(probabilities, output_directory)
    generate_simulations(simulations, output_directory)

    if generate_projects(project, config, args.scenario, output_directory):
        if not args.no_queries and len(queries) > 0:
            projects, queries_numbers = output_folder_queries(output_directory)
            print_queries(run_all_queries(args.verifyta, projects, queries_numbers), queries)
        if not args.no_probabilities and len(probabilities) > 0:
            projects, probabilities_number = output_folder_probabilities(output_directory)
            print_probabilities(run_all_probabilities(args.verifyta, projects, probabilities_number), probabilities)
        if not args.no_simulations and len(simulations) > 0:
            projects, simulations_number = output_folder_simulations(output_directory)
            print_simulations(run_all_simulations(args.verifyta, projects, simulations_number), simulations, result_directory)

    shutil.rmtree(output_directory)
    if len(os.listdir(result_directory)) == 0:
        shutil.rmtree(result_directory)
