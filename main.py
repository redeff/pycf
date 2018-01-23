#!/usr/bin/env python3
from pathlib import Path
import requests
import http.cookiejar as cookielib
from bs4 import BeautifulSoup
import asyncio
import argparse
import re
import os
import copy
import concurrent.futures
import datetime
import time
from colorama import init, Fore, Back, Style
init()


cf_page = 'http://codeforces.com'
config_dir = os.path.expanduser('~/proj/pycf/dep')
# work_dir = os.path.expanduser('~/cmp/cf')
work_dir = os.path.expanduser('~/proj/pycf/test')
html_wrap_template = config_dir + '/html_wrap_template.html'

class Session:
    def __init__(self, cookiefile = None):
        self.session = requests.Session()
        if cookiefile != None:
            self.transient = False
            self.session.cookies = cookielib.LWPCookieJar(cookiefile)
            try:
                self.session.cookies.load()
            except:
                pass
        else:
            self.transient = True


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
    def login(self, user, passwd):
        csrf = self.csrf_from_page(BeautifulSoup(self.session.get(cf_page).content, 'html.parser'))
        response = self.session.post(url=cf_page + "/enter", data={
            "action": "enter", 
            "handle": "plugin_test", 
            "password": "throwaway", 
            "remember": "true", 
            "csrf_token": csrf
        })
        if response.status_code != 200:
            raise Exception("Login unsuccessful. Check provided username and password")
        if not self.transient:
            self.session.cookies.save()


    # submit a problem to codeforces under a login session
    # filename = path to the source file you want to submit
    # contest = contest id number, generally 3 digits long. Visible in the contest url
    # problem = 'A', 'B', 'C', ... . The letter of the problem
    # lang = optional programming language of submission, if blank it'll be
    #   inferred using the extension
    # session = login session obtaines by a call to login()
    def submit(self, filename, problem, lang = None):
        if lang == None:
            lang = self.infer_from_extension(os.path.splitext(filename)[1])
        submit_url = cf_page + '/contest/' + problem.contest + '/submit'
        csrf = self.csrf_from_page(BeautifulSoup(self.session.get(submit_url).content, 'html.parser'))
        response = self.session.post(url=submit_url, data={
            'csrf_token': csrf,
            'action': 'submitSolutionFormSubmitted',
            'submittedProblemIndex': problem.problem,
            'programTypeId': lang,
        }, files={
            'source': open(filename, 'rb')
        })
        if response.status_code != 200:
            raise Exception("Submission failed")
        if not self.transient:
            self.session.cookies.save()

class Problem:
    def __init__(self, contest, problem):
        self.contest = contest
        self.problem = problem

        self.page_soup = None
        self.statement = None
        self.images = {} 
        self.test_cases = None

    def get_statement(self):
        if self.page_soup == None:
            self.download_page()

        # if self.statement != None:
        #     return self.statement

        unwrapped_statement = self.page_soup.find(attrs={'class': 'problem-statement'})
        my_wrapper = copy.copy(self.wrap_template)

        try:
            my_wrapper.find(class_='template-replace').replace_with(copy.copy(unwrapped_statement))
        except:
            print(wrap_template.prettify())


        counter = 0
        for img in my_wrapper.find_all('img'):
            new_url = str(counter) + '.png'
            self.images[new_url] = img['src']
            img['src'] = new_url 
            counter += 1

        return my_wrapper

    def get_images(self):
        if self.images != None:
            return self.images 

        self.get_statement()
        return self.images

    def get_test_cases(self):
        if self.page_soup == None:
            self.download_page()

        test_inputs = []
        for inp in self.page_soup.find_all('div', class_='input'):
            inp = inp.find('pre')
            for br in inp.find_all('br'):
                br.replace_with('\n')

            test_inputs.append(inp.text)

        test_outputs = []
        for out in self.page_soup.find_all('div', class_='output'):
            out = out.find('pre')
            for br in inp.find_all('br'):
                br.replace_with('\n')

            test_outputs.append(out.text)

        return (test_inputs, test_outputs)

    def download_page(self):
        page = requests.get(cf_page + '/contest/' + self.contest + '/problem/' + self.problem)
        self.page_soup = BeautifulSoup(page.content, 'html.parser')

    async def download_images(self):
        loop = asyncio.get_event_loop()
        futures = [
                loop.run_in_executor(
                    None,
                    requests.get,
                    cf_page + '/' + url
                )
                for p, url in self.get_images().items()
        ]
        total = [] 
        for response in await asyncio.gather(*futures):
            total.append(response.content)

        return total

    def save(self):
        path_to_prob = '%s/%s/%s/' % (work_dir, self.contest, self.problem)
        Path(path_to_prob).mkdir(parents=True, exist_ok=True)
        Path(path_to_prob + 'statement/').mkdir(exist_ok=True)
        open(path_to_prob + 'statement/index.html', 'w').write(self.get_statement().prettify())

        loop = asyncio.get_event_loop()
        images = loop.run_until_complete(self.download_images())
        
        # for (path, url) in self.get_images().items():
        #    open(path_to_prob + 'statement/' + path, 'wb').write(requests.get(cf_page + '/' + url).content)
        for (idx, path) in enumerate(self.get_images()):
           open(path_to_prob + 'statement/' + path, 'wb').write(images[idx])

        (test_inp, test_out) = self.get_test_cases()
        for idx, inp in enumerate(test_inp):
            open(path_to_prob + str(idx) + '.in', 'w').write(inp)

        for idx, out in enumerate(test_out):
            open(path_to_prob + str(idx) + '.out', 'w').write(out)


Problem.wrap_template = BeautifulSoup(open(html_wrap_template, 'r').read(), 'html.parser') 

class Contest:
    def __init__(self, contest):
        self.contest = contest
        self.page_soup = None

    def download_page(self):
        page = requests.get(cf_page + '/contest/' + self.contest)
        self.page_soup = BeautifulSoup(page.content, 'html.parser')

    def get_problems(self):
        if self.page_soup == None:
            self.download_page()

        for problem in self.page_soup.find_all('td', class_='id'):
            yield problem.find('a').text.strip()

    def save(self):
        for p in problems

class Info:
    @classmethod
    def str_single_contest(cls, contest, colored):
        rel_time = datetime.timedelta(seconds=-contest["relativeTimeSeconds"])
        ini_time = contest["startTimeSeconds"]
        dur = datetime.timedelta(seconds=contest["durationSeconds"])
        name = contest["name"]
        yd = contest["id"]
        if colored:
            return Style.BRIGHT + Fore.MAGENTA + str(yd) + Style.RESET_ALL + ' : ' +\
                Fore.CYAN + name + Style.RESET_ALL + '\n ' +\
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
    def get_upcoming_contests(cls, colored=True):
        total = ''
        contests = requests.get(cf_page + '/api/contest.list').json()["result"];
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
            return Contest(last)
        path, first = os.path.split(str(path))
        if all(c.isdigit() for c in first) and all(c.isupper() for c in last):
            return Problem(first, last)

        return None

# class Contest:
#     pass


### SUBMIT
# submit('test.cpp', '784', 'A', login('plugin_test', 'throwaway'))
# se = Session(cookiefile='cookies.txt')
# se = Session()
# se.login('plugin_test', 'throwaway')
# se.submit('test.cpp', '781', 'A')

# prob = Problem('741', 'A')
# print(prob.get_statement().prettify())
# print(prob.get_test_cases())
# cont = Contest('700')
# for p in cont.get_problems():
#     print(p)
# prob = Problem('741', 'A')
# prob.save()
# print(Info.get_upcoming_contests())

def ls(args):
    print(Info.get_upcoming_contests())

def sub(args):
    infer = Infer.infer_dir()
    if type(infer) is Problem:
        session = Session(cookiefile = args.c)
        session.submit('main.cpp', infer)
    else:
        print("problem couldn't be inferred from context")

def login(args):
    session = Session(cookiefile = args.c)
    session.login(args.user, args.pasw)

def do(args):
    

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
parser_do.add_argument('problem')
parser_do.set_defaults(func=do)


args = parser.parse_args()
args.func(args)
