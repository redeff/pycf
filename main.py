#!/usr/bin/env pipenv run python
from pathlib import Path
# import requests
import http.cookiejar as cookielib
from bs4 import BeautifulSoup
import asyncio
import aiohttp
import argparse
import re
import os
import copy
import concurrent.futures
import datetime
import time
import libtmux
from subprocess import run
import subprocess
from colorama import init, Fore, Back, Style
init()


cf_page = 'http://codeforces.com'
config_dir = os.path.expanduser('~/proj/pycf/dep')
work_dir = os.path.expanduser('~/cmp/cf')
# work_dir = os.path.expanduser('~/proj/pycf/test')
html_wrap_template = config_dir + '/html_wrap_template.html'

# Codeforces Session
class Session:
    def __init__(self):
        self.session = aiohttp.ClientSession()

    async def __aenter__(self):
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.__aexit__(exc_type, exc, tb)

    def save(self, cookiefile):
        self.session.cookie_jar.save(cookiefile)

    def load(self, cookiefile):
        self.session.cookie_jar.load(cookiefile)

    # Given the downloaded source of a page, extract the csrf (cross-site request forgery)
    # protection token to authenticate many requests
    @staticmethod
    def csrf_from_page(soup_page):
        # Beutiful Soup
        try:
            return soup_page.find(attrs={'name': 'csrf_token'})['value']
        except:
            print(soup_page.prettify())

    @staticmethod
    def infer_from_extension(ext):
        if ext == '.cpp':
            return 42
        else:
            raise Exception("Unsupported language supplied: " + ext)

    # returns session object for further authenticated requests
    # provide codeforces username & password
    async def login(self, user, passwd):
        async with self.session.get(cf_page) as csrf_page:
            csrf = self.csrf_from_page(BeautifulSoup(await csrf_page.read(), 'html.parser'))

            payload = {
                "action": "enter", 
                "handle": "plugin_test", 
                "password": "throwaway", 
                "remember": "true", 
                "csrf_token": csrf
            }
            async with self.session.post(url=cf_page + "/enter", data=payload) as response:
                if response.status != 200:
                    raise Exception("Login unsuccessful. Check provided username and password")

    # submit a problem to codeforces under a login session
    # filename = path to the source file you want to submit
    # contest = contest id number, generally 3 digits long. Visible in the contest url
    # problem = 'A', 'B', 'C', ... . The letter of the problem
    # lang = optional programming language of submission, if blank it'll be
    #   inferred using the extension
    # session = login session obtaines by a call to login()
    async def submit(self, filename, problem, lang = None):
        if lang == None:
            lang = self.infer_from_extension(os.path.splitext(filename)[1])
        submit_url = cf_page + '/contest/' + problem.contest + '/submit'
        async with self.session.get(submit_url) as csrf_page:
            csrf = self.csrf_from_page(BeautifulSoup(await csrf_page.read(), 'html.parser'))

            payload = {
                'csrf_token': csrf,
                'action': 'submitSolutionFormSubmitted',
                'submittedProblemIndex': problem.problem,
                'programTypeId': str(lang),
                'source': open(filename, 'rb')
            }

            async with self.session.post(url=submit_url, data=payload) as response:
                if response.status != 200:
                    raise Exception("Submission failed")

# dumb_session = aiohttp.ClientSession()

# Problem download dependency graph
# ALL < statement, test_cases
# STATEMENT < statement_text, statement_images
# STATEMENT_TEXT < @problem_page
# STATEMENT_IMAGES < @image_page < image_url < @problem_page
def colorcode(s):
    output = ''
    for c in s:
        if c == '0':
            output += '\033[2m0\033[0m'
        elif c == '1':
            output += '\033[31m1\033[0m'
        elif c == '2':
            output += '\033[32m2\033[0m'
        elif c == '3':
            output += '\033[34m3\033[0m'
        elif c == '4':
            output += '\033[33m4\033[0m'
        elif c == '5':
            output += '\033[91m5\033[0m'
        elif c == '6':
            output += '\033[36m6\033[0m'
        elif c == '7':
            output += '\033[95m7\033[0m'
        elif c == '8':
            output += '\033[35m8\033[0m'
        elif c == '9':
            output += '\033[37m9\033[0m'
        else:
            output += c
    return output

def indent(s, ind):
    s = s.rstrip()
    if s == '' or s[-1] != '\n':
        s += '\n'
    out = ind
    was_n = False
    for c in s:
        if was_n and c != '\n':
            out += ind

        was_n = c == '\n'

        out += c


    return out


class Problem:

    wrap_template = BeautifulSoup(open(html_wrap_template, 'r').read(), 'html.parser') 

    def __init__(self, contest, problem, session = None):
        self.session = session
        self.contest = contest
        self.problem = problem
        self.raw_statement = None
        self.images = [] # From expected filename to bytestring of the actual image
        self.image_urls = []
        self.wrapped_statement = None
        self.in_test_cases = None
        self.out_test_cases = None

    async def get_raw_statement_and_image_urls(self):
        if self.raw_statement != None:
            return self.raw_statement, self.image_urls

        async with self.session.get(cf_page + '/contest/' + self.contest + '/problem/' + self.problem) as page:
            page = BeautifulSoup(await page.read(), 'html.parser')
            self.raw_statement = page.find(class_='problem-statement')

            counter = 0
            for img in self.raw_statement.find_all('img'):
                new_url = str(counter) + '.png'
                self.image_urls.append((new_url, img['src']))
                img['src'] = new_url
                counter += 1

            return self.raw_statement, self.image_urls

    async def get_raw_statement(self):
        return (await self.get_raw_statement_and_image_urls())[0]

    async def get_image_urls(self):
        return (await self.get_raw_statement_and_image_urls())[1]

    async def get_wrapped_statement(self):
        if self.wrapped_statement != None:
            return self.wrapped_statement

        my_wrapper = copy.copy(self.wrap_template)
        my_wrapper.find(class_='template-replace').replace_with(copy.copy(await self.get_raw_statement()))
        self.wrapped_statement = my_wrapper.prettify()
        return self.wrapped_statement 

    async def get_images(self):
        async def do_task(p):
            # print('getting image ' + p[1])
            async with self.session.get(cf_page + '/' + p[1]) as page:
                res = p[0], await page.read()
                # print('ready image' + p[1])
                return res
        self.images = await asyncio.gather(*map(do_task, self.image_urls))
        return self.images

    async def get_testcases(self):
        test_inputs = []
        for inp in (await self.get_raw_statement()).find_all('div', class_='input'):
            inp = inp.find('pre')
            for br in inp.find_all('br'):
                br.replace_with('\n')

            test_inputs.append(inp.text)

        test_outputs = []
        for out in (await self.get_raw_statement()).find_all('div', class_='output'):
            out = out.find('pre')
            for br in inp.find_all('br'):
                br.replace_with('\n')

            test_outputs.append(out.text)

        self.in_test_cases = test_inputs
        self.out_test_cases = test_outputs

        return (test_inputs, test_outputs)

    async def download(self):
        await self.get_wrapped_statement()
        await self.get_images()
        await self.get_testcases()
        return self

    def save(self, to_dir=work_dir):
        path_to_prob = '%s/%s/%s/' % (to_dir, self.contest, self.problem)
        Path(path_to_prob).mkdir(parents=True, exist_ok=True)
        Path(path_to_prob + 'statement/').mkdir(exist_ok=True)
        open(path_to_prob + 'statement/index.html', 'w').write(self.wrapped_statement)

        for path, img in self.images:
           open(path_to_prob + 'statement/' + path, 'wb').write(img)

        for idx, inp in enumerate(self.in_test_cases):
            open(path_to_prob + str(idx) + '.in', 'w').write(inp)

        for idx, out in enumerate(self.out_test_cases):
            open(path_to_prob + str(idx) + '.out', 'w').write(out)


class Contest:
    def __init__(self, contest, session):
        self.contest = contest
        self.page = None
        self.problems = None 
        self.session = session

    async def download_page(self):
        async with self.session.get(cf_page + '/contest/' + self.contest) as page:
            self.page = BeautifulSoup(await page.text(), 'html.parser')

    async def download(self):
        await asyncio.gather(*map(lambda x: x.download(), await self.get_problem_names()))
        return self

    async def get_problem_names(self):
        if self.page == None:
            await self.download_page()

        if self.problems != None:
            return self.problems

        self.problems = []

        for problem in self.page.find_all('td', class_='id'):
            self.problems.append(Problem(self.contest, problem.find('a').text.strip(), self.session))

        return self.problems

    def save(self):
        for p in self.problems:
            p.save()

# async def call():
#     async with aiohttp.ClientSession() as s:
#         # await s.login("plugin_test", "throwaway")
#         # await s.submit("main.cpp", Problem("722", "B"))
#         (await Contest('741', s).download()).save()
# 
# loop = asyncio.get_event_loop()
# loop.run_until_complete(call())

class Info:
    @classmethod
    def str_single_contest(cls, contest, colored):
        rel_time = datetime.timedelta(seconds=-contest["relativeTimeSeconds"])
        ini_time = contest["startTimeSeconds"]
        dur = datetime.timedelta(seconds=contest["durationSeconds"])
        name = contest["name"]
        yd = contest["id"]
        if colored:
            return '\033[31m' + str(yd) + Style.RESET_ALL + ' : ' +\
                '\033[91m' + name + Style.RESET_ALL + '\n ' +\
                Fore.BLUE + 'in ' + str(rel_time) +\
                Fore.GREEN + ' at ' + time.strftime('%Y-%m-%d %H:%M:%S', datetime.datetime.fromtimestamp(ini_time).timetuple()) +\
                Fore.YELLOW + ' lasts ' + str(dur)
        else:
            return str(yd) + ' : ' +\
                name + '\n ' +\
                'in ' + str(rel_time) +\
                ' at ' + time.strftime('%Y-%m-%d %H:%M:%S', datetime.datetime.fromtimestamp(ini_time).timetuple()) +\
                ' lasts ' + str(dur)

    @classmethod
    async def get_upcoming_contests(cls, session, colored=True):
        total = ''
        async with session.get(cf_page + '/api/contest.list') as contests:
            contests = await contests.json()
            contests = contests['result']
            for contest in contests:
                if contest["phase"] != 'FINISHED':
                    total += cls.str_single_contest(contest, colored) + '\n'
            return total[:-1]

class Infer:
    @classmethod
    def infer_dir(cls, path=None):
        if path == None:
            path = str(os.getcwd())

        path, last = os.path.split(str(path))
        if all(c.isdigit() for c in last):
            return Contest(last, None)
        path, first = os.path.split(str(path))
        if all(c.isdigit() for c in first) and all(c.isupper() for c in last):
            return Problem(first, last, None)

        return None

    @classmethod
    def latest_in_dir(cls, path=work_dir):
        dirs = [os.path.join(path, d) for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
        latest_modified = max(dirs, key=lambda x: os.path.getmtime(x))
        return latest_modified


async def ls(args):
    async with aiohttp.ClientSession() as session:
        print(await Info.get_upcoming_contests(session))

async def sub(args):
    infer = Infer.infer_dir()
    if type(infer) is Problem:
        async with Session() as session:
            if os.path.isfile(args.c):
                session.load(args.c)
            await session.submit('main.cpp', infer)
            session.save(args.c)
    else:
        print("problem couldn't be inferred from context")

async def login(args):
    async with Session() as session:
        if os.path.isfile(args.c):
            session.load(args.c)
        await session.login(args.user, args.pasw)
        session.save(args.c)

async def do(args):
    async with aiohttp.ClientSession() as session:
        (await Contest(args.contest, session).download()).save()

async def tmux(args):
    last_contest = None
    if args.contest:
        last_contest = os.path.join(work_dir, args.contest)
    else:
        last_contest = Infer.latest_in_dir()

    cont = Infer.infer_dir(last_contest)
    server = libtmux.Server()
    name = "cf-" + cont.contest

    session = None
    try:
        session = server.find_where({'session_name': name})
    except libtmux.exc.LibTmuxException:
        session = None

    if session == None:
        session = server.new_session(session_name=name, detach=True)
    else:
        print("Error, session already found ", session)
        # session = server.new_session(session_name=name, detach=True)

    for d in os.listdir(last_contest):
        w = session.new_window(attach=False, window_name=d)
        comp = w.split_window(attach=False, vertical=False)
        code = w.attached_pane
        code.send_keys('cd ' + os.path.join(last_contest, d))
        comp.send_keys('cd ' + os.path.join(last_contest, d))
        comp.send_keys('cmpc main.cpp', enter=False)
        code.send_keys('vim main.cpp')

async def test(args):
    try:
        if os.path.getmtime('main.cpp') > os.path.getmtime('prog'):
            run(['/bin/bash', '-i', '-c', 'cmpc main.cpp -o prog'])
    except FileNotFoundError:
        run(['/bin/bash', '-i', '-c', 'cmpc main.cpp -o prog'])

    i = 0

    def wrap(s, col):
        return indent(colorcode(s), '  \033[' + col + '|\033[0m ')

    while os.path.isfile(str(i) + '.in'):
        print('## TEST ' + str(i) + ' ##')
        # print('\033[32m   input====\033[0m' \
        #        + indent(colorcode(), '\033[32m')[12:])
        print(wrap(open(str(i) + '.in').read(), '32m<'))
        # print('\033[34m   output===\033[0m' \
        #         + indent(colorcode(), '\033[34m')[12:])
        print(wrap((run(['./prog', '<', str(i) + '.in'], stdout=subprocess.PIPE).stdout).decode('utf-8'), '34m>'))
        if os.path.isfile(str(i) + '.out'):
            # print('\033[33m   expected=\033[0m' \
            #         + indent(colorcode(), '\033[33m')[12:])
            print(wrap(open(str(i) + '.out').read(), '33m?'))

        i += 1

parser = argparse.ArgumentParser(description='Codeforces cli tool')
parser.add_argument('-c', default='/home/redeff/proj/pycf/cookies.txt')
subparsers = parser.add_subparsers()

parser_ls = subparsers.add_parser('ls')
parser_ls.set_defaults(func=ls)

parser_sub = subparsers.add_parser('sub')
parser_sub.set_defaults(func=sub)

parser_login = subparsers.add_parser('login')
parser_login.add_argument('user')
parser_login.add_argument('pasw')
parser_login.set_defaults(func=login)

parser_do = subparsers.add_parser('do');
parser_do.add_argument('contest')
parser_do.set_defaults(func=do)

parser_tmux = subparsers.add_parser('tmux')
parser_tmux.set_defaults(func=tmux)
parser_tmux.add_argument('contest', nargs='?')
parser_tmux.add_argument('problem', nargs='?')

parser_test = subparsers.add_parser('test')
parser_test.set_defaults(func=test)

args = parser.parse_args()
loop = asyncio.get_event_loop()
loop.run_until_complete(args.func(args))
