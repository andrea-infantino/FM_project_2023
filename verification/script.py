#!/usr/bin/env python3

# Inspired by: https://github.com/leonardobarilani/warehouse-model-checking/blob/main/src/simulation/sim.py

import argparse
import json
import os
from multiprocessing import Pool
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

def get_queries(content: str) -> list[str]:
    tree = ET.fromstring(content)
    queries = []
    for formula in tree.iter('formula'):
        if not formula.text == None:
            queries.append(formula.text)
    return queries

def generate_formulas(queries: list[str], path: str):
    index = 0
    for query in queries:
        with open(os.path.join(path, 'query_{:02d}.txt'.format(index)), 'w') as file:
            file.write('{}\n'.format(query))
        index += 1

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

def parse_output_folder(path: str) -> list[list[str]]:
    projects = []
    queries = []
    for item in os.listdir(path):
        if item.startswith('project'):
            projects.append(os.path.join(path, item))
        else:
            queries.append(os.path.join(path, item))
    return projects, queries

def run_query(parameters: list[tuple[str, str, str]]) -> bool:
    start = time()
    verifier, project, query = parameters
    result = subprocess.run([verifier, project, query], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print('[ERROR]', result.stderr.decode().rstrip())
        exit(1)
    return 'Formula is satisfied' in result.stdout.decode(), project.split('/')[-1], query.split('/')[-1], time() - start

def run_all(verifier: str, projects: list[str], queries: list[str]) -> dict:
    pool = Pool(os.cpu_count())
    args = [(verifier, project, query) for project in projects for query in queries]
    results = {}
    print('\033[;1m')
    for result, project, query, time in tqdm(pool.imap_unordered(run_query, args), desc='Verifying', total=len(args)):
        project = project.strip('project_').strip('.xml')
        query = query.strip('query_').strip('.txt')
        results[(project, query)] = (result, '{0:.2f} seconds'.format(time))
    pool.close()
    pool.join()
    print('\033[0m')
    return results

def print_results(results: dict, queries: str):
    projects = {}
    failed = False
    for project, query in results:
        if project not in projects:
            projects[project] = {}
        result, time = results[(project, query)]
        projects[project][query] = {'query': queries[int(query)], 'result': result, 'time': time}
        if not result:
            failed = True
    for project in projects:
        projects[project] = dict(sorted(projects[project].items()))
    projects = dict(sorted(projects.items()))
    if failed:
        print('\033[31;1mSome properties aren\'t satisfied!\033[0m\n')
    else:
        print('\033[32;1mAll the properties are satisfied!\033[0m\n')
    print('\033[;1mThis is the result of the verification in JSON format\033[0m:')
    print(json.dumps(projects))
    print()

if __name__ == '__main__':

    output_directory = 'tmp'

    signal.signal(signal.SIGINT, handle_sigint)
    if os.path.isdir(output_directory):
        shutil.rmtree(output_directory)
    os.makedirs(output_directory)

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
    queries = get_queries('\n'.join(project))

    generate_formulas(queries, output_directory)
    if generate_projects(project, config, args.scenario, output_directory):
        projects, queries_numbers = parse_output_folder(output_directory)
        print_results(run_all(args.verifyta, projects, queries_numbers), queries)
    
    shutil.rmtree(output_directory)
