#!/usr/bin/env python
import argparse
import subprocess
import os
import sys
import json
import tempfile
import datetime

LOCAL_ROOT = os.path.join(os.environ['HOME'], '.vy')
DEFAULT_FEATURE = 'noname'
SETUP_ERROR = RuntimeError('Environment is not set up. Invoke "vy setup"')

# todo error handling
REMOTE_SETUP = '''#!/bin/sh
export PATH=/usr/local/bin:$PATH

# init sync repo
git init --bare {repo}

# link sync repo with svn-wc
git --git-dir {root} --work-tree {dir} init
echo '.svn' >> {root}/info/exclude
git --git-dir {root} --work-tree {dir} remote add origin {repo}
git --git-dir {root} --work-tree {dir} add .
rev=$( svn info {dir} | grep Revision | cut -d' ' -f2 )
git --git-dir {root} --work-tree {dir} commit -m "svn rev $rev"
git --git-dir {root} --work-tree {dir} fetch --all
#git --git-dir {root} --work-tree {dir} branch develop
git --git-dir {root} --work-tree {dir} checkout -b develop
git --git-dir {root} --work-tree {dir} push --set-upstream origin develop
'''

# todo use master for sync
# todo use workflow in setup
# todo hide .svn: rename .svn
def execute(cmd):
    print cmd
    parts = cmd.split('"')
    result = []
    for i in xrange(0, len(parts), 2):
        result += [s for s in parts[i].split(' ') if s]
        if i+1 < len(parts):
            result += [parts[i+1]]

    subprocess.Popen(result).communicate()


class LocalGitWorkflow(object):
    def __init__(self, repo_dir):
        self.git_dir = os.path.join(repo_dir, '.git')
        self.work_tree = repo_dir
        self.commands = []

    def add(self, cmd):
        self.commands.append(cmd)

    def execute(self):
        for cmd in self.commands:
            git_cmd = 'git --git-dir {} --work-tree {} {}'.format(self.git_dir, self.work_tree, cmd)
            execute(git_cmd)


class RemoteGitWorkflow(object):
    def __init__(self, server, work_tree, profile, name):
        self.server = server
        self.work_tree = work_tree
        self.root = os.path.join(get_remote_home(server), '.vy.remote', profile)
        self.git_dir = os.path.join(self.root, '.git')
        self.commands = []
        self.name = name

    def add(self, cmd):
        self.commands.append(cmd)

    def execute(self):
        with tempfile.NamedTemporaryFile() as script:
            for cmd in self.commands:
                git_cmd = 'git --git-dir {} --work-tree {} {}\n'.format(self.git_dir, self.work_tree, cmd)
                script.write(git_cmd)
            script.file.flush()
            remote_script = os.path.join(self.root, '{}_remote.sh'.format(self.name))
            execute('scp {} {}:{}'.format(script.name, self.server, remote_script))
            execute('ssh {} sh -e {}'.format(self.server, remote_script))


def get_remote_home(host):
    stdout, _ = subprocess.Popen('ssh {} pwd'.format(host).split(' '), stdout=subprocess.PIPE).communicate()
    return stdout.strip()


def setup_command(args):
    config = get_config(args.profile, fail=False)
    if config is None:
        make_profile(args.profile)
        config = get_config(args.profile)

    srv = config['remote-server']
    remote_root = os.path.join(get_remote_home(srv), '.vy.remote', args.profile)
    local_end = config['local-dir']
    remote_end = config['remote-dir']
    repo_filepath = os.path.join(remote_root, 'media')
    repo_ssh = 'ssh://{host}{path}'.format(host=srv, path=repo_filepath)
    remote_script = os.path.join(remote_root, 'setup_remote.sh')
    remote_git_dir = os.path.join(remote_root, '.git')

    # remote sync-repo setup
    script_tmp = tempfile.NamedTemporaryFile()
    script_tmp.write(REMOTE_SETUP.format(dir=remote_end, repo=repo_filepath, root=remote_git_dir))
    script_tmp.file.flush()

    execute('ssh {} rm -rf {}'.format(srv, remote_root))
    execute('ssh {} mkdir -p {}'.format(srv, repo_filepath))
    execute('scp {} {}:{}'.format(script_tmp.name, srv, remote_script))
    execute('ssh {} sh -e {}'.format(srv, remote_script))

    # local repo setup
    execute('rm -rf {}'.format(local_end))
    execute('mkdir -p {}'.format(local_end))
    execute('git clone {remote} {local}'.format(remote=repo_ssh, local=local_end))

    local_git_dir = os.path.join(local_end, '.git')
    execute('git --git-dir {} --work-tree {} fetch'.format(local_git_dir, local_end))
    execute('git --git-dir {} --work-tree {} checkout develop'.format(local_git_dir, local_end))
    make_feature_dir(args.profile, DEFAULT_FEATURE)
    set_feature_branch(args.profile, DEFAULT_FEATURE)
    print 'Success'


def get_commit_message(user_msg):
    msg = datetime.datetime.now().strftime("%d.%m.%Y %H:%M%:%S")
    if user_msg:
        msg += ' ({})'.format(user_msg)
    return msg


def make_feature_dir(profile, feature):
    feature_dir = os.path.join(LOCAL_ROOT, profile, feature)
    if not os.path.exists(feature_dir):
        os.makedirs(feature_dir)


def set_feature_branch(profile, feature):
    config = get_config(profile)

    local_git = LocalGitWorkflow(config['local-dir'])
    local_git.add('checkout develop')
    local_git.add('branch {}'.format(feature))
    local_git.add('checkout {}'.format(feature))
    local_git.execute()

    config['feature'] = feature
    save_profile(profile, config)


def push_command(args):
    config = get_config(args.profile)
    feature = config['feature']

    # todo reuse set_feature_branch
    local_git = LocalGitWorkflow(config['local-dir'])
    local_git.add('branch {} develop'.format(feature))
    local_git.add('checkout {}'.format(feature))
    local_git.add('add .')
    local_git.add('commit -m "{}"'.format(get_commit_message(args.message)))
    local_git.add('push --set-upstream origin {}'.format(feature))
    local_git.execute()

    remote_git = RemoteGitWorkflow(config['remote-server'], config['remote-dir'], args.profile, 'push')
    remote_git.add('fetch --all')
    remote_git.add('checkout {}'.format(feature))
    remote_git.add('clean -fd'.format(feature))
    remote_git.add('checkout .'.format(feature))
    remote_git.add('pull')
    remote_git.execute()

    print 'Success'


def switch_command(args):
    config = get_config(args.profile)
    if args.feature == config['feature']:
        print 'Already in {}'.format(args.feature)
        return


def list_features(profile):
    cfg = get_config(profile)
    features = next(os.walk(os.path.join(LOCAL_ROOT, profile)))[1]
    for feature in features:
        print 'Features of profile "{}":'.format(profile)
        if feature == cfg['feature']:
            print '* ' + feature
        else:
            print '  ' + feature


def ls_command(args):
    if not os.path.exists(LOCAL_ROOT):
        raise SETUP_ERROR

    if args.all:
        for profile in os.listdir(LOCAL_ROOT):
            list_features(profile)
    else:
        list_features(args.profile)


def good_path(path, home=None):
    if home is None:
        path = os.path.expanduser(path)
    elif path.startswith('~'):
        path.replace('~', home)
    path = os.path.normpath(path)
    return os.path.abspath(path)


def save_profile(name, cfg):
    cfg_path = os.path.join(LOCAL_ROOT, name, 'cfg')
    with open(cfg_path, 'w+') as out:
        json.dump(cfg, out, indent=2)


def make_profile(name):
    cfg_path = os.path.join(LOCAL_ROOT, name, 'cfg')
    if os.path.exists(cfg_path):
        raise RuntimeError('Configuration is already set at {}'.format(cfg_path))

    os.makedirs(os.path.join(LOCAL_ROOT, name))
    cfg = {}
    sys.stdout.write('Local directory: ')
    cfg['local-dir'] = good_path(sys.stdin.readline().strip())

    sys.stdout.write('Remote server: ')
    cfg['remote-server'] = sys.stdin.readline().strip()

    sys.stdout.write('Remote directory: ')
    remote_home = get_remote_home(cfg['remote-server'])
    cfg['remote-dir'] = good_path(sys.stdin.readline().strip(), remote_home)

    save_profile(name, cfg)
    print 'Configuration has been saved to {}'.format(cfg_path)


def get_config(profile_name, fail=True):
    path = os.path.join(LOCAL_ROOT, profile_name, 'cfg')
    if not os.path.exists(path):
        if fail is True:
            raise SETUP_ERROR
        else:
            return None

    with open(path) as input:
        return json.load(input)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument('--profile', '-p', help='profile name', default='default', metavar='NAME')
    # TODO more width
    subparsers = parser.add_subparsers(help='List of commands')

    fmt = argparse.ArgumentDefaultsHelpFormatter

    setup_parser = subparsers.add_parser('setup', help='create dirs and repositories', formatter_class=fmt)
    setup_parser.set_defaults(func=setup_command)

    push_parser = subparsers.add_parser('push', help='push local changes to remote dir', formatter_class=fmt)
    push_parser.add_argument('--message', '-m', help='push message', metavar='TEXT')
    push_parser.set_defaults(func=push_command)

    switch_parser = subparsers.add_parser('switch', help='switch to another feature', formatter_class=fmt)
    switch_parser.add_argument('feature', help='feature name', metavar='NAME')
    switch_parser.set_defaults(func=switch_command)

    info_parser = subparsers.add_parser('ls', help='list features & profiles', formatter_class=fmt)
    info_parser.add_argument('--all', '-a', help='list profiles and features', action='store_true')
    info_parser.set_defaults(func=ls_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
