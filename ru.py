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
MAINSTREAM_BRANCH = 'mainstream'

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
    remote.git('add .')
    remote.git('commit -m "$msg"')
    remote.git('fetch --all')
    remote.git('checkout -b {}'.format(MAINSTREAM_BRANCH))
    remote.git('push --set-upstream origin {}'.format(MAINSTREAM_BRANCH))
    remote.execute()

    # local repo setup
    local = LocalWorkflow(local_end)
    local.reset()
    repo_ssh = 'ssh://{host}{path}'.format(host=srv, path=repo_filepath)

    local.git('clone {remote} {local}'.format(remote=repo_ssh, local=local_end), location=False)
    local.git('fetch --all')
    local.execute()

    goto_feature(args.profile, DEFAULT_FEATURE)


def goto_git_branch_remote(config, profile, branch):
    remote = RemoteWorkflow(config['remote-server'], config['remote-dir'], profile, 'go')
    remote.git('fetch --all')
    remote.git('checkout -B {0} --track origin/{0}'.format(branch))
    remote.git('clean -fd'.format(branch)) # todo find out is it obligatory? seems checkout <feature> do the job
    remote.git('checkout .'.format(branch))
    remote.execute()


def goto_feature(profile, feature):
    config = get_config(profile)

    new = make_feature_dir(profile, feature)
    local = LocalWorkflow(config['local-dir'])
    local.git('fetch --all')
    if new:
        local.git('checkout -B {} origin/{}'.format(feature, MAINSTREAM_BRANCH))
    else:
        local.git('checkout {}'.format(feature))
    local.git('clean -fd'.format(feature))
    local.git('checkout .'.format(feature))

    if not new:
        local.git('merge origin/{0} -m "Merge {0}"'.format(MAINSTREAM_BRANCH))
    local.git('push --set-upstream origin {}'.format(feature)) # todo try remove it
    local.execute()

    # todo detect manual svn up
    # todo make conflict resolving if devel is updated
    goto_git_branch_remote(config, profile, feature)

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
    #config = get_config(args.profile)
    goto_feature(args.profile, args.feature)


def update_command(args):
    config = get_config(args.profile)
    subsystem = get_subsystem(config['subsystem'], config['remote-dir'])
    remote = RemoteWorkflow(config['remote-server'], config['remote-dir'], args.profile, 'pull')

    goto_git_branch_remote(config, args.profile, MAINSTREAM_BRANCH)
    subsystem.update_mainstream(remote)
    remote.git('add .')
    subsystem.commit_message(remote)
    remote.git('commit -m "$msg"  --allow-empty')
    remote.git('push')
    remote.execute()

    goto_feature(args.profile, config['feature'])


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

    go_parser = subparsers.add_parser('go', help='go to another feature', formatter_class=fmt)
    go_parser.add_argument('feature', help='feature name', metavar='NAME')
    go_parser.set_defaults(func=go_command)

    ls_parser = subparsers.add_parser('ls', help='list features & profiles', formatter_class=fmt)
    ls_parser.add_argument('--all', '-a', help='list profiles and features', action='store_true')
    ls_parser.set_defaults(func=ls_command)

    update_parser = subparsers.add_parser('update', help='update mainstream', formatter_class=fmt)
    update_parser.set_defaults(func=update_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
