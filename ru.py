#!/usr/bin/env python
import argparse
import subprocess
import os
import sys
import json
import tempfile
import datetime
import shutil

LOCAL_ROOT = os.path.join(os.environ['HOME'], '.gru_local')
SETUP_ERROR = RuntimeError('Environment is not set up. Invoke "gru setup"')
REMOTE_ROOT_DIR = '.gru_remote'
SYNC_BRANCH = 'master'

# todo use master for sync
# todo hide .svn: rename .svn
# todo separate svn logic
# todo how to be if user wants to svn up to older revision

# todo idea: no mainstream, only features, manual svn up, manual svn conflict resolution
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
                script.write('echo "{}"\n'.format(cmd))
                script.write(cmd + '\n')
            script.file.flush()
            remote_script = os.path.join(self.__root, '{}.sh'.format(self.name))
            execute('scp {} {}:{}'.format(script.name, self.server, remote_script))
            execute('ssh {} sh -e {}'.format(self.server, remote_script))


class RemoteSubsystem(object):
    """
    Describes a nature of remote dir content
    """

    TYPE = None

    def __init__(self, folder):
        pass

    def excludes(self, remote, exclude_file):
        assert False, 'Not implemented'

    def commit_message(self, remote):
        assert False, 'Not implemented'

    def update_mainstream(self, remote):
        assert False, 'Not implemented'


class RemoteFiles(RemoteSubsystem):
    TYPE = 'files'

    def __init__(self, folder):
        super(RemoteFiles, self).__init__(folder)

    def excludes(self, remote, exclude_file):
        pass

    def commit_message(self, remote):
        remote.sh('msg="files sync"')

    def update_mainstream(self, remote):
        pass


class RemoteSvn(RemoteSubsystem):
    TYPE = 'svn'

    def __init__(self, folder):
        super(RemoteSvn, self).__init__(folder)
        self.wc = folder

    def excludes(self, remote, exclude_file):
        remote.sh('echo .svn >> {}'.format(exclude_file))

    def commit_message(self, remote):
        remote.sh('msg="SVN r$( svn info {} | grep Revision | cut -d" " -f2 )"'.format(self.wc))

    def update_mainstream(self, remote):
        remote.sh('svn up {}'.format(self.wc))


def get_subsystem(ss_type, folder):
    for ss in (RemoteFiles, RemoteSvn):
        if ss.TYPE == ss_type:
            return ss(folder)
    raise RuntimeError('Subsystem of type "{}" is not found'.format(ss_type))


def get_remote_home(host):
    stdout, _ = subprocess.Popen('ssh {} pwd'.format(host).split(' '), stdout=subprocess.PIPE).communicate()
    return stdout.strip()


def get_timestamp_message(user_msg):
    msg = datetime.datetime.now().strftime("%d.%m.%Y %H:%M%:%S")
    if user_msg:
        msg += ' ({})'.format(user_msg)
    return msg


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

    cfg['subsystem'] = args.subsystem
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


def setup_command(args):
    make_profile(args)
    config = get_config(args.profile)

    srv = config['remote-server']
    local_end = config['local-dir']
    remote_end = config['remote-dir']
    subsystem = get_subsystem(config['subsystem'], remote_end)

    # remote sync-repo setup
    remote = RemoteWorkflow(srv, remote_end, args.profile, 'setup')
    repo_filepath = os.path.join(remote.root, 'media')
    remote_git_dir = os.path.join(remote.root, '.git')

    remote.reset()
    remote.sh('mkdir -p {}'.format(repo_filepath))
    remote.git('init --bare {}'.format(repo_filepath), location=False)
    remote.git('init')
    subsystem.excludes(remote, '{}/info/exclude'.format(remote_git_dir))
    subsystem.commit_message(remote)
    remote.git('remote add origin {}'.format(repo_filepath))
    remote.git('fetch --all')
    remote.git('checkout -B {}'.format(SYNC_BRANCH))
    remote.git('add .')
    remote.git('commit -m "[remote] $msg"')
    remote.git('push --set-upstream origin {}'.format(SYNC_BRANCH))
    remote.execute()

    # local repo setup
    local = LocalWorkflow(local_end)
    local.reset()
    repo_ssh = 'ssh://{host}{path}'.format(host=srv, path=repo_filepath)

    local.git('clone {remote} {local}'.format(remote=repo_ssh, local=local_end), location=False)
    local.git('fetch --all')
    local.git('checkout -B {0}'.format(SYNC_BRANCH))
    local.git('branch --set-upstream-to=origin/{0} {0}'.format(SYNC_BRANCH))
    local.git('pull')
    local.execute()

# todo hide .git from work copy
def push_command(args):
    config = get_config(args.profile)

    # bring potential conflicts on local side
    remote = RemoteWorkflow(config['remote-server'], config['remote-dir'], args.profile, 'push')
    remote.git('add .')
    msg = 'save potentially overwritten changes'
    remote.git('commit -m "[remote] {}" --allow-empty'.format(get_timestamp_message(msg)))
    remote.git('push')
    remote.execute()

    local = LocalWorkflow(config['local-dir'])
    local.git('add .')
    local.git('commit -m "[local] {}" --allow-empty'.format(get_timestamp_message(args.message)))
    local.git('pull')
    local.git('push')
    local.execute()

    remote.git('pull')
    remote.execute()

"""
local edit
remote svn up
gru pull
conflicts??? -- make user to resolve it

local edit
remote svn up
gru push
conflicts??? (remote conflicts -- worse)

right way:
1. local edit
2. gru push
3. remote svn up
4. gru pull
"""
def pull_command(args):
    config = get_config(args.profile)

    srv, remote_dir = config['remote-server'], config['remote-dir']
    remote = RemoteWorkflow(srv, remote_dir, args.profile, 'pull')
    remote.git('add .')
    remote.git('commit -m "update from {}:{}" --allow-empty'.format(srv, remote_dir))
    remote.git('push')
    remote.execute()

    local = LocalWorkflow(config['local-dir'])
    local.git('add .')
    msg = get_timestamp_message('save potentially overwritten changes')
    local.git('commit -m "[local] {}" --allow-empty'.format(msg))
    local.git('pull')
    local.execute()


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
    setup_parser.add_argument('--subsystem', choices=['svn', 'files'], help='remote subsystem', metavar='NAME',
                              default='files')
    setup_parser.set_defaults(func=setup_command)

    push_parser = subparsers.add_parser('push', help='push local changes to remote dir', formatter_class=fmt)
    push_parser.add_argument('--message', '-m', help='push message', metavar='TEXT')
    push_parser.set_defaults(func=push_command)

    pull_parser = subparsers.add_parser('pull', help='pull remote changes to local dir', formatter_class=fmt)
    pull_parser.set_defaults(func=pull_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
