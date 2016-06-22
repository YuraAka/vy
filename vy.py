#!/usr/bin/env python
import argparse
import subprocess
import os
import sys
import json
import tempfile

# todo error handling
REMOTE_SCRIPT = '''#!/bin/sh
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
git --git-dir {root} --work-tree {dir} fetch
#git --git-dir {root} --work-tree {dir} branch develop
git --git-dir {root} --work-tree {dir} checkout -b develop
git --git-dir {root} --work-tree {dir} push --set-upstream origin develop
'''


def execute(cmd):
    print cmd
    subprocess.Popen(cmd.split(' ')).communicate()    


def get_remote_home(host):
    stdout, _ = subprocess.Popen('ssh {} pwd'.format(host).split(' '), stdout=subprocess.PIPE).communicate()
    return stdout.strip()


def setup(args):
    # echo .svn > .gitignore
    config = get_config(args.config)
    srv = config['remote-server']
    # todo more flexible
    remote_root = os.path.join(get_remote_home(srv), '.vy.remote', os.path.basename(args.config))
    local_end = config['local-dir']
    remote_end = config['remote-dir']
    repo_filepath = os.path.join(remote_root, 'git-repo')
    repo_ssh = 'ssh://{host}{path}'.format(host=srv, path=repo_filepath)
    remote_script = os.path.join(remote_root, 'setup_remote.sh')
    remote_git_dir = os.path.join(remote_root, '.git')

    # remote sync-repo setup
    script_tmp = tempfile.NamedTemporaryFile()
    script_tmp.write(REMOTE_SCRIPT.format(dir=remote_end, repo=repo_filepath, root=remote_git_dir))
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

    print 'Setup has finished'


def config(args):
    cfg_path = args.file
    if os.path.exists(cfg_path):
        raise RuntimeError('Configuration is already set at {}'.format(cfg_path))
    cfg = {}
    sys.stdout.write('Local directory: ')
    cfg['local-dir'] = os.path.expanduser(sys.stdin.readline().strip())

    sys.stdout.write('Remote server: ')
    cfg['remote-server'] = sys.stdin.readline().strip()

    sys.stdout.write('Remote directory: ')
    cfg['remote-dir'] = os.path.expanduser(sys.stdin.readline().strip())

    with open(cfg_path, 'w+') as out:
        json.dump(cfg, out, indent=2)
    print 'Configuration has been saved to {}'.format(cfg_path)


def get_config(path):
    # todo handle path not exist
    with open(path) as input:
        return json.load(input)


def main():
    parser = argparse.ArgumentParser()

    # TODO more width
    subparsers = parser.add_subparsers(help='List of commands')

    default_cfg_path = os.path.join(os.environ['HOME'], '.vy')
    fmt = argparse.ArgumentDefaultsHelpFormatter

    config_parser = subparsers.add_parser('config', help='create a configuration file', formatter_class=fmt)

    config_parser.set_defaults(func=config)
    help_str = 'destination to save configuration'
    config_parser.add_argument('--file', '-f', help=help_str, default=default_cfg_path, metavar='PATH')

    setup_parser = subparsers.add_parser('setup', help='create dirs and repositories', formatter_class=fmt)
    setup_parser.set_defaults(func=setup)
    help_str = 'path to configuration file'
    setup_parser.add_argument('--config', '-c', help=help_str, default=default_cfg_path, metavar='PATH')

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
