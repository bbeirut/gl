#!/usr/bin/env python3

'''
Usage:
	gl [-o | -c | -a] [-r]
	gl ((open | o) | <issue-number>)
	gl (reopen | r | close | c | edit | e ) <issue-number>
	gl assign <issue-number> <username>

'''


import os
import sys
import shutil
from uuid import uuid4 as randomid

import git # git-repo
import gitlab
from gitlab.exceptions import GitlabGetError
from gitlab import ProjectIssue

from datetime import datetime
import humanize
import textwrap
from termcolor import colored, cprint

import logging
_log = logging.getLogger(__name__)
_log.setLevel(logging.DEBUG)
console_logger = logging.FileHandler('/tmp/gitlab.log')
console_logger.setLevel(logging.DEBUG)
_log.addHandler(console_logger)

config = ''

DEBUG = True

from urllib.parse import urlparse
try:
	from colors.colortrans import rgb2short, background_contrast
except:
	from .colors.colortrans import rgb2short, background_contrast

DEFAULT_EDITOR = 'vim'
GITLAB_PICKLE_FILE = '.gitlab.pkl'
ISSUE_DATE_FORMAT  = '%Y-%m-%dT%H:%M:%S.%fZ'
ISSUE_EDIT_MSG     = textwrap.fill((
	'# Explain the issue. The first line is its title. '
	'Subsequent lines are the issue’s description. '
	'Lines starting with # are ignored and empty messages will not be submitted. '
	'Issues are formatted in Markdown.'
	'\n'
	'\n'
	'# vim:ft=gitcommit'
	), width=72, subsequent_indent='# ', replace_whitespace=False)

TERMINAL_FORMAT = {
		'normal': '\033[0m',
		'bold': '\033[1m'
		}

def in_colors(color, text):
	if not isinstance(color, list):
		color = [7, color]
	# TODO: raise an exception if color has out-of-range elements (0-255)
	return '\033[38;5;{color[0]}m\033[48;5;{color[1]}m{text}\033[0m'.format(**locals())

class Object():

	def __init__(self, dictionary={}):
		for key, value in dictionary.items():
			setattr(self, key, value)

	def __repr__(self):
		attrs = '\n'.join(['{key}: {value}' for key, value in self.__dict__.items()])
		return '<Object: {attrs}>'.format(**locals())

def read_config():
	repo = git.Repo(os.getcwd(), search_parent_directories=True)
	import configparser
	config = configparser.ConfigParser()
	config_file = os.path.join(repo.working_tree_dir, '.gitlab.ini')
	if os.path.exists(config_file):
		config.read(config_file)
	return repo, { k: config['gitlab'].get(k, None) for k in ['server', 'url', 'private_token'] }

class GitLabCommand():
	def __init__(self, repo, config=None):
		self.repo = repo
		if config:
			if config['server']:
				self.gitlab = gitlab.Gitlab.from_config(config['server'])
			elif config['url']:
				self.gitlab = gitlab.Gitlab(url=config['url'])
				self.gitlab.set_token(config['private_token'])
			else:
				sys.exit('Missing authentication data.')
		else:
			self.gitlab = gitlab.Gitlab.from_config()
		self.gitlab_hostname = urlparse(self.gitlab._url).hostname
		self.user = gitlab.objects.CurrentUser(self.gitlab)
		project_path = self._get_gitlab_project_path()
		try:
			self.project = self.gitlab.projects.get(project_path)
		except GitlabGetError:
			sys.exit(GitlabGetError)
		self._init_labels()
		self._set_issues()

	def _set_issues(self):
		self.issues = {}

	def _save(self):
		import pickle
		pickled_object_filename = os.path.join(self.repo.working_tree_dir, GITLAB_PICKLE_FILE)
		with open(pickled_object_filename, 'wb') as pickled_object_file:
			pickle.dump(self, pickled_object_file)

	def _init_from_pickle(self):
		import pickle
		repo = git.Repo(os.getcwd(), search_parent_directories=True)
		pickled_object_file = os.path.join(repo.working_tree_dir, GITLAB_PICKLE_FILE)
		if os.path.isfile(pickled_object_file):
			return repo, pickle.load(open(pickled_object_file, 'rb'))
		return repo, False

	def _gitlab_project_path_from_remote(self, remote):
		if type(remote) == str: # remote is a remote name
			try:
				remote = self.repo.remote(remote)
			except ValueError:
				return None

		for url in remote.urls:
			parsed_url = urlparse(url)
			if not parsed_url.scheme: # it must be ssh
				parsed_url = urlparse('ssh://%s' % url)
			if parsed_url.hostname != self.gitlab_hostname:
				continue # go to next url
			username = parsed_url.netloc.split(':')[-1]
			project_name = parsed_url.path[:parsed_url.path.rfind('.git')]
			project_path = username + project_name
			return project_path


	def _get_gitlab_project_path(self):
		# loop through remotes looking for one that matches the gitlab_hostname
		# start with `upstream` and `origin` then loop through all remotes
		priority_remotes = ['upstream', 'origin']
		remote = None
		project_path = None
		for remote in priority_remotes + self.repo.remotes:
			project_path = self._gitlab_project_path_from_remote(remote)
			if project_path:
				return project_path
		sys.exit('Could not find GitLab remote repository.')

	def _format_user(self, user):
		if (user.id == self.user.id):
			name      = 'me'
			color     = 'green'
		else:
			name  = user.username
			color = 'cyan'
		return colored('@{name}'.format(**locals()), color)

	def _format_labels(self, issue):
		display_labels = []
		for label_name in issue.labels:
			display_labels.append(self.labels[label_name])
		return ' '.join(display_labels)

	def _format_issue_line(self, issue):
		# issue number
		issue_num = colored('{issue.iid: >4}'.format(**locals()), attrs=['bold'])

		# TODO: implement milestones
		assignee    = self._format_user(issue.assignee) if issue.assignee else ''
		labels      = self._format_labels(issue)
		title_width = round(shutil.get_terminal_size().columns * 2/3)
		return '{issue_num}: {issue.title:.{title_width}} {labels} {assignee} '.format(**locals())

	def _init_labels(self):
		STATUS_LABELS = [
			{
				'name': 'opened',
				'color': '#008000'
			},
			{
				'name': 'closed',
				'color': '#ff0000'
			},
			{
				'name': 'reopened',
				'color': '#bee933'
			},
		]
		self.labels = {}
		for label in STATUS_LABELS + self.project.labels.list():
			if isinstance(label, dict):
				label = Object(label)
			if label.name in self.labels:
				continue
			short_color, rgb = rgb2short(label.color)
			self.labels[label.name] = in_colors(color=[background_contrast(rgb), short_color], text=label.name)

	def _print_labels(self):
		for name, text in self.labels.items():
			print(text)


	def _detail_view(self, issue):
		from issue import format_description
		# title line
		# #295: Photo Pane: Make the remaining tools work [closed]
		state_label = self.labels[issue.state]
		#	title_line = colored('{issue.iid: >4}: {issue.title} {state_label}'.format(**locals()), attrs=['bold'])
		# open line
		# opened by @eva 79 days ago.
		author = self._format_user(issue.author)
		created_at = datetime.strptime(issue.created_at, ISSUE_DATE_FORMAT)
		created_at_humanized = humanize.naturaltime(created_at)
		# assignee line
		# assigned to @jack
		assignee = 'assigned to {}'.format(self._format_user(issue.assignee)) if issue.assignee else ''
		labels = self._format_labels(issue)

		max_width = round(shutil.get_terminal_size().columns * 0.75)
		# description = re.sub('\r\n', '\n', issue.description, re.MULTILINE)
		description = format_description(issue, indent=2)
		FORMAT = TERMINAL_FORMAT

		detail_view = '''
{FORMAT[bold]}#{issue.iid}: {issue.title} {FORMAT[normal]}{state_label}
{FORMAT[normal]}
opened by {author} {created_at_humanized}
{assignee} {labels}


{description}'''.format(**locals())
		return detail_view


	def reopen_issue(self, issue_num):
		issue = self.get_issue_or_exit(issue_num)
		issue.state_event = 'reopen'
		issue.save()

	def close_issue(self, issue_num):
		issue = self.get_issue_or_exit(issue_num)
		issue.state_event = 'close'
		issue.save()

	def _parse_issue_file(self, issue_filename):
		issue_lines = None
		with open(issue_filename) as issue_file:
			# exclude comment lines
			issue_lines = [line for line in issue_file.readlines() if not line.lstrip().startswith('#')]
		if not issue_lines:
			return {}
		title = issue_lines.pop(0)[:-1]
		description = ''.join(issue_lines).strip()
		import re
		# remove any space characters between paragraph separators (2 linebreaks)
		description = re.sub(r'\n\s+\n', '\n\n', description, re.MULTILINE)
		# replace single linebreaks with space as those are just line wraps
		description = re.sub(r'(?<!\n)\n(?!\n)', ' ', description, re.MULTILINE)
		# description = re.sub(r'\n', '\n\n', description, re.MULTILINE)
		return {'title': title, 'description': description}

	def create_issue(self):
		self._edit_or_create_issue()

	def edit_issue(self, issue_num):
		self._edit_or_create_issue(issue_num)

	def _edit_or_create_issue(self, issue_num=None):
		from issue import format_description
		issue = self.get_issue_or_exit(issue_num) if issue_num else None
		while True:
			random_filename = 'gl-issue-{}'.format(str(randomid()))
			random_filename_fullpath = os.path.join(self.repo.working_dir, '.git', random_filename)
			if not os.path.exists(random_filename_fullpath):
				break
		with open(random_filename_fullpath, 'w') as issue_file:
			if issue:
				title       = textwrap.fill('{issue.title}'.format(**locals()), width=72)
				description = format_description(issue)
				issue_file.write('{title}\n\n{description}'.format(**locals()))

			issue_file.write(2 * '\n' + ISSUE_EDIT_MSG)
		import subprocess
		editor = os.getenv('EDITOR', DEFAULT_EDITOR)
		p = subprocess.Popen([editor, random_filename_fullpath])
		p.wait()
		parsed_issue = self._parse_issue_file(random_filename_fullpath)
		if 'title' not in parsed_issue or not parsed_issue['title']:
			sys.stdout.write('Aborted — empty issue.')
			sys.exit(0)

		if issue_num: # edited existing issue
			changed = issue.title != parsed_issue['title'] or issue.description != parsed_issue['description']
			if not changed:
				sys.stdout.write('Aborted — no changes made.')
				sys.exit(0)
			issue.title = parsed_issue['title']
			issue.description = parsed_issue['description']
		else: # created new issue
			issue = parsed_issue

		self._submit_issue(issue, random_filename_fullpath)

	def _submit_issue(self, issue, issue_filename):
		try:
			if isinstance(issue, ProjectIssue):
				issue.save()
				print('#{issue.iid} updated.'.format(**locals()))
			else:
				created_issue = self.project.issues.create(issue)
				print('#{created_issue.iid} created.'.format(**locals()))
			os.remove(issue_filename)
		except Exception as e:
			issue_filename_relative_path = os.path.relpath(issue_filename, self.repo.working_dir)
			sys.stderr.write('Could not submit issue. Issue saved in {issue_filename_relative_path}.'.format(**locals()))
			sys.exit(1)

	# TODO:
	#   create branch from issue

	def list_issues(self, issue_num=None, order_by='created_at', state='opened', reverse=False):
		if not issue_num:
			# list view
			if not state:
				issues = self.project.issues.list(order_by=order_by)
			else:
				issues = self.project.issues.list(order_by=order_by, state=state)
			if reverse:
				issues.reverse()
			for issue in issues:
				cprint(self._format_issue_line(issue))
			return
		# detail view
		issue = self.get_issue_or_exit(issue_num)
		print(self._detail_view(issue))

	def get_issue_or_exit(self, issue_num):
		try:
			issue = self.issues.get(issue_num, None) or self.project.issues.get('', iid=issue_num)
		except GitlabGetError:
			sys.stderr.write('Invalid issue #{issue_num}.'.format(**locals()))
			sys.exit(1)
		return issue


def main():
	from docopt import docopt
	# parse command line args
	args = docopt(__doc__)


	repo, config = read_config()
	gitlab_command = GitLabCommand(repo, config)

	if args['open'] or args['o']:
		return gitlab_command.create_issue()
	issue_num = args['<issue-number>']
	if issue_num:
		try:
			int(issue_num)
		except ValueError:
			print(__doc__)
			sys.exit(1)

	if args['reopen'] or args['r']:
		return gitlab_command.reopen_issue(issue_num)
	if args['close'] or args['c']:
		return gitlab_command.close_issue(issue_num)
	if args['edit'] or args['e']:
		return gitlab_command.edit_issue(issue_num)

	reverse = True if args['-r'] else False
	state = 'opened'
	if args['-a']:
		state = None
	elif args['-c']:
		state = 'closed'

	return gitlab_command.list_issues(issue_num=issue_num, state=state, reverse=reverse)

if __name__ == '__main__':
	main()
