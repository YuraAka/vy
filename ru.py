#!/usr/bin/env python
import argparse
import subprocess
import os
import sys
import json
import tempfile
import datetime
import shutil

LOCAL_ROOT = os.path.join(os.environ['HOME'], '.ru')
DEFAULT_FEATURE = 'noname'
SETUP_ERROR = RuntimeError('Environment is not set up. Invoke "ru setup"')
REMOTE_ROOT_DIR = '.ru.remote'

# todo use master for sync
# todo hide .svn: rename .svn
# todo separate svn logic
def execute(cmd):
    print cmd
    parts = cmd.split('"')
    result = []
    for i in xrange(0, len(parts), 2):
        result += [s for s in parts[i].split(' ') if s]
        if i+1 < len(parts):
            result += [parts[i+1]]

    p = subprocess.Popen(result)
    p.communicate()
    if p.returncode != 0:
        raise RuntimeError('ERROR: "{}" exists with code {}'.format(cmd, p.returncode))


class LocalWorkflow(object):
    def __init__(self, repo_dir):
        self.git_dir = os.path.join(repo_dir, '.git')
        self.work_tree = repo_dir
        self.commands = []

    def reset(self):
        self.sh('rm -rf {}'.format(self.work_tree))
        self.sh('mkdir -p {}'.format(self.work_tree))

    def git(self, cmd, location=True):
        if location:
            git_cmd = 'git --git-dir {} --work-tree {} {}'.format(self.git_dir, self.work_tree, cmd)
        else:
            git_cmd = 'git {}'.format(cmd)
        self.commands.append(git_cmd)

    def sh(self, cmd):
        self.commands.append(cmd)

    def execute(self):
        for cmd in self.commands:
            execute(cmd)


class RemoteWorkflow(object):
    @property
    def root(self):
        return self.__root

    def __init__(self, server, work_tree, profile, name):
        self.server = server
        self.work_tree = work_tree
        self.__root = os.path.join(get_remote_home(server), REMOTE_ROOT_DIR, profile)
        self.git_dir = os.path.join(self.__root, '.git')
        self.commands = ['export PATH=/usr/local/bin:$PATH']
        self.name = name

    def reset(self):
        execute('ssh {} rm -rf {}'.format(self.server, self.__root))
        execute('ssh {} mkdir -p {}'.format(self.server, self.__root))

    def git(self, cmd, location=True):
        if location:
            git_cmd = 'git --git-dir {} --work-tree {} {}'.format(self.git_dir, self.work_tree, cmd)
        else:
            git_cmd = 'git {}'.format(cmd)

        self.commands.append(git_cmd)

    def sh(self, cmd):
        self.commands.append(cmd)

    def execute(self):
        with tempfile.NamedTemporaryFile() as script:
            script.write('set -e\n')
            for cmd in self.commands:
                script.write('echo {}\n'.format(cmd))
                script.write(cmd + '\n')
            script.file.flush()
            remote_script = os.path.join(self.__root, '{}.sh'.format(self.name))
            execute('scp {} {}:{}'.format(script.name, self.server, remote_script))
            execute('ssh {} sh -e {}'.format(self.server, remote_script))


def get_remote_home(host):
    stdout, _ = subprocess.Popen('ssh {} pwd'.format(host).split(' '), stdout=subprocess.PIPE).communicate()
    return stdout.strip()


def setup_command(args):
    make_profile(args)
    config = get_config(args.profile)

    srv = config['remote-server']
    local_end = config['local-dir']
    remote_end = config['remote-dir']

    # remote sync-repo setup
    remote = RemoteWorkflow(srv, remote_end, args.profile, 'setup')
    repo_filepath = os.path.join(remote.root, 'media')
    remote_git_dir = os.path.join(remote.root, '.git')

    remote.reset()
    remote.sh('mkdir -p {}'.format(repo_filepath))
    remote.git('init --bare {}'.format(repo_filepath), location=False)
    remote.git('init')
    remote.git('remote add origin {}'.format(repo_filepath))
    remote.git('add .')
    remote.sh('echo ".svn" >> {}/info/exclude'.format(remote_git_dir))
    remote.sh('rev=$( svn info {} | grep Revision | cut -d" " -f2 )'.format(remote_end)) # todo unlink
    remote.git('commit -m "svn rev $rev"')
    remote.git('fetch --all')
    remote.git('checkout -b develop')
    remote.git('push --set-upstream origin develop')
    remote.execute()

    # local repo setup
    local = LocalWorkflow(local_end)
    local.reset()
    repo_ssh = 'ssh://{host}{path}'.format(host=srv, path=repo_filepath)

    local.git('clone {remote} {local}'.format(remote=repo_ssh, local=local_end), location=False)
    local.git('fetch --all')
    local.git('checkout develop')
    local.execute()
    goto_feature(args.profile, DEFAULT_FEATURE)


def get_commit_message(user_msg):
    msg = datetime.datetime.now().strftime("%d.%m.%Y %H:%M%:%S")
    if user_msg:
        msg += ' ({})'.format(user_msg)
    return msg


def make_feature_dir(profile, feature):
    feature_dir = os.path.join(LOCAL_ROOT, profile, feature)
    if not os.path.exists(feature_dir):
        os.makedirs(feature_dir)
        return True
    return False


def goto_feature(profile, feature):
    config = get_config(profile)

    new = make_feature_dir(profile, feature)
    local = LocalWorkflow(config['local-dir'])
    if new:
        local.git('checkout -B {} develop'.format(feature))
    else:
        local.git('checkout {}'.format(feature))
    local.git('clean -fd'.format(feature))
    local.git('checkout .'.format(feature))
    local.git('push --set-upstream origin {}'.format(feature))
    local.execute()

    # todo detect manual svn up
    remote = RemoteWorkflow(config['remote-server'], config['remote-dir'], profile, 'go')
    remote.git('fetch --all')
    remote.git('checkout -B {0} --track origin/{0}'.format(feature))
    remote.git('clean -fd'.format(feature))
    remote.git('checkout .'.format(feature))
    remote.execute()

    config['feature'] = feature
    save_config(profile, config)


def push_command(args):
    config = get_config(args.profile)
    feature = config['feature']

    local = LocalWorkflow(config['local-dir'])
    local.git('checkout {}'.format(feature))
    local.git('add .')
    local.git('commit -m "{}" --allow-empty'.format(get_commit_message(args.message)))
    local.git('push')
    local.execute()

    remote = RemoteWorkflow(config['remote-server'], config['remote-dir'], args.profile, 'push')
    remote.git('pull')
    remote.execute()


def pull_command(args):
    config = get_config(args.profile)
    feature = config['feature']

    remote = RemoteWorkflow(config['remote-server'], config['remote-dir'], args.profile, 'pull')
    remote.git('add .')
    remote.git('commit -m "pull" --allow-empty')
    remote.git('push')
    remote.execute()

    local = LocalWorkflow(config['local-dir'])
    local.git('checkout {}'.format(feature))
    local.git('pull')
    local.execute()


def go_command(args):
    config = get_config(args.profile)
    if args.feature != config['feature']:
        goto_feature(args.profile, args.feature)


def list_features(profile):
    cfg = get_config(profile)
    features = next(os.walk(os.path.join(LOCAL_ROOT, profile)))[1]
    print 'Features of profile "{}":'.format(profile)
    for feature in features:
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


def save_config(name, cfg):
    cfg_path = os.path.join(LOCAL_ROOT, name, 'cfg')
    with open(cfg_path, 'w+') as out:
        json.dump(cfg, out, indent=2)


def make_profile(args):
    name = args.profile
    profile_path = os.path.join(LOCAL_ROOT, name)
    cfg_path = os.path.join(profile_path, 'cfg')
    shutil.rmtree(profile_path, ignore_errors=True)

    os.makedirs(profile_path)
    cfg = {}
    if args.local_dir is None:
        sys.stdout.write('Local directory: ')
        cfg['local-dir'] = good_path(sys.stdin.readline().strip())
    else:
        cfg['local-dir'] = good_path(args.local_dir)

    if args.remote_server is None:
        sys.stdout.write('Remote server: ')
        cfg['remote-server'] = sys.stdin.readline().strip()
    else:
        cfg['remote-server'] = args.remote_server

    remote_home = get_remote_home(cfg['remote-server'])
    if args.remote_dir is None:
        sys.stdout.write('Remote directory: ')
        cfg['remote-dir'] = good_path(sys.stdin.readline().strip(), remote_home)
    else:
        cfg['remote-dir'] = good_path(args.remote_dir, remote_home)

    save_config(name, cfg)
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
    setup_parser.add_argument('--local-dir', help='path to local dir (must not exist)', metavar='PATH')
    setup_parser.add_argument('--remote-server', help='server name or ip', metavar='NAME')
    setup_parser.add_argument('--remote-dir', help='path to remote dir', metavar='PATH')
    setup_parser.set_defaults(func=setup_command)

    push_parser = subparsers.add_parser('push', help='push local changes to remote dir', formatter_class=fmt)
    push_parser.add_argument('--message', '-m', help='push message', metavar='TEXT')
    push_parser.set_defaults(func=push_command)

    pull_parser = subparsers.add_parser('pull', help='pull remote changes to local dir', formatter_class=fmt)
    pull_parser.set_defaults(func=pull_command)

    go_parser = subparsers.add_parser('go', help='go to another feature', formatter_class=fmt)
    go_parser.add_argument('feature', help='feature name', metavar='NAME')
    go_parser.set_defaults(func=go_command)

    ls_parser = subparsers.add_parser('ls', help='list features & profiles', formatter_class=fmt)
    ls_parser.add_argument('--all', '-a', help='list profiles and features', action='store_true')
    ls_parser.set_defaults(func=ls_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
