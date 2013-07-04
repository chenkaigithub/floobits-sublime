# coding: utf-8
import sys
import os
import hashlib
import imp
import json
import subprocess
import traceback
import webbrowser
from collections import defaultdict

import sublime_plugin
import sublime

try:
    import ssl
    assert ssl
except ImportError:
    ssl = False

if ssl is False and sublime.platform() == 'linux':
    plugin_path = os.path.split(os.path.dirname(__file__))[0]
    if plugin_path in ('.', ''):
        plugin_path = os.getcwd()
    _ssl = None
    ssl_versions = ['0.9.8', '1.0.0', '10']
    ssl_path = os.path.join(plugin_path, 'lib', 'linux')
    lib_path = os.path.join(plugin_path, 'lib', 'linux-%s' % sublime.arch())
    for version in ssl_versions:
        so_path = os.path.join(lib_path, 'libssl-%s' % version)
        try:
            filename, path, desc = imp.find_module('_ssl', [so_path])
            if filename is None:
                print('Module not found at %s' % so_path)
                continue
            _ssl = imp.load_module('_ssl', filename, path, desc)
            break
        except ImportError as e:
            print('Failed loading _ssl module %s: %s' % (so_path, unicode(e)))
    if _ssl:
        print('Hooray! %s is a winner!' % so_path)
        filename, path, desc = imp.find_module('ssl', [ssl_path])
        if filename is None:
            print("Couldn't find ssl module at %s" % ssl_path)
        else:
            try:
                ssl = imp.load_module('ssl', filename, path, desc)
            except ImportError as e:
                print('Failed loading ssl module at: %s' % unicode(e))
    else:
        print("Couldn't find an _ssl shared lib that's compatible with your version of linux. Sorry :(")


try:
    from urllib.error import HTTPError
    from .floo import api, AgentConnection, listener, msg, shared as G, utils
    from .floo.listener import Listener
    assert HTTPError and api and AgentConnection and G and Listener and listener and msg and utils
except (ImportError, ValueError):
    from urllib2 import HTTPError
    from floo import api, AgentConnection, listener, msg, utils
    from floo.listener import Listener
    from floo import shared as G


PY2 = sys.version_info < (3, 0)

settings = sublime.load_settings('Floobits.sublime-settings')

DATA = {}
ON_CONNECT = None
FLOORC_PATH = os.path.expanduser('~/.floorc')
G.BASE_DIR = os.path.expanduser(os.path.join('~', 'floobits'))
# TODO: one day this can be removed (once all our users have updated)
old_colab_dir = os.path.realpath(os.path.expanduser(os.path.join('~', '.floobits')))
if os.path.isdir(old_colab_dir) and not os.path.exists(G.BASE_DIR):
    print('renaming %s to %s' % (old_colab_dir, G.BASE_DIR))
    os.rename(old_colab_dir, G.BASE_DIR)
    os.symlink(G.BASE_DIR, old_colab_dir)


def update_recent_workspaces(workspace):
    d = utils.get_persistent_data()
    recent_workspaces = d.get('recent_workspaces', [])
    recent_workspaces.insert(0, workspace)
    recent_workspaces = recent_workspaces[:25]
    seen = set()
    new = []
    for r in recent_workspaces:
        stringified = json.dumps(r)
        if stringified not in seen:
            new.append(r)
            seen.add(stringified)

    DATA['recent_workspaces'] = new
    utils.update_persistent_data(d)


def load_floorc():
    """try to read settings out of the .floorc file"""
    s = {}
    try:
        fd = open(FLOORC_PATH, 'rb')
    except IOError as e:
        if e.errno == 2:
            return s
        raise

    default_settings = fd.read().decode('utf-8').split('\n')
    fd.close()

    for setting in default_settings:
        # TODO: this is horrible
        if len(setting) == 0 or setting[0] == '#':
            continue
        try:
            name, value = setting.split(' ', 1)
        except IndexError:
            continue
        s[name.upper()] = value
    return s


def reload_settings():
    global settings
    print('Reloading settings...')
    # TODO: settings doesn't seem to load most settings.
    # Also, settings.get('key', 'default_value') returns None
    settings = sublime.load_settings('Floobits.sublime-settings')
    G.ALERT_ON_MSG = settings.get('alert_on_msg')
    if G.ALERT_ON_MSG is None:
        G.ALERT_ON_MSG = True
    G.LOG_TO_CONSOLE = settings.get('log_to_console')
    if G.LOG_TO_CONSOLE is None:
        G.LOG_TO_CONSOLE = False
    G.DEBUG = settings.get('debug')
    if G.DEBUG is None:
        G.DEBUG = False
    G.COLAB_DIR = settings.get('share_dir') or os.path.join(G.BASE_DIR, 'share')
    G.COLAB_DIR = os.path.expanduser(G.COLAB_DIR)
    G.COLAB_DIR = os.path.realpath(G.COLAB_DIR)
    utils.mkdir(G.COLAB_DIR)
    G.DEFAULT_HOST = settings.get('host') or 'floobits.com'
    G.DEFAULT_PORT = settings.get('port') or 3448
    G.SECURE = settings.get('secure')
    if G.SECURE is None:
        G.SECURE = True
    G.USERNAME = settings.get('username')
    G.SECRET = settings.get('secret')
    floorc_settings = load_floorc()
    for name, val in floorc_settings.items():
        setattr(G, name, val)
    if G.AGENT and G.AGENT.is_ready():
        msg.log('Reconnecting due to settings change')
        G.AGENT.reconnect()
    print('Floobits debug is %s' % G.DEBUG)


settings.add_on_change('', reload_settings)
reload_settings()


def get_legacy_projects():
    a = ['msgs.floobits.log', 'persistent.json']
    owners = os.listdir(G.COLAB_DIR)
    floorc_json = defaultdict(defaultdict)
    for owner in owners:
        if len(owner) > 0 and owner[0] == '.':
            continue
        if owner in a:
            continue
        workspaces_path = os.path.join(G.COLAB_DIR, owner)
        try:
            workspaces = os.listdir(workspaces_path)
        except OSError:
            continue
        for workspace in workspaces:
            workspace_path = os.path.join(workspaces_path, workspace)
            workspace_path = os.path.realpath(workspace_path)
            try:
                fd = open(os.path.join(workspace_path, '.floo'), 'rb')
                url = json.loads(fd.read())['url']
                fd.close()
            except Exception:
                url = utils.to_workspace_url({
                    'port': 3448, 'secure': True, 'host': "floobits.com", 'owner': owner, 'workspace': workspace
                })
            floorc_json[owner][workspace] = {
                'path': workspace_path,
                'url': url
            }

    return floorc_json


def migrate_symlinks():
    data = {}
    old_path = os.path.join(G.COLAB_DIR, 'persistent.json')
    if not os.path.exists(old_path):
        return
    old_data = utils.get_persistent_data(old_path)
    data['workspaces'] = get_legacy_projects()
    data['recent_workspaces'] = old_data.get('recent_workspaces')
    utils.update_persistent_data(data)
    try:
        os.unlink(old_path)
        os.unlink(os.path.join(G.COLAB_DIR, 'msgs.floobits.log'))
    except Exception:
        pass
    return json.dumps(data, indent=2)

print(migrate_symlinks())


INITIAL_FLOORC = """# Hello!
#
# Thank you for installing Floobits. To join and share workspaces, you'll need to finish configuring it.
# Floobits reads configuration settings from ~/.floorc. You didn't have a ~/.floorc file, so we created it.
#
# If everything has gone according to plan, your browser will open
# https://floobits.com/dash/initial_floorc/. That page will show you the settings to put in
# this file.
#
# For more help, see https://floobits.com/help/plugins/#sublime-text
#
# If you have any problems or questions, please e-mail support@floobits.com
# -- The Floobits Team
#
#
######  UNCOMMENT AND CHANGE THE LINES BELOW  ######

# username your_username
# secret your_api_secret

######  UNCOMMENT AND CHANGE THE LINES ABOVE  ######
"""


def get_active_window(cb):
    win = sublime.active_window()
    if not win:
        return utils.set_timeout(get_active_window, 50, cb)
    cb(win)


def initial_run():
    timeout = 0
    if not os.path.exists(FLOORC_PATH):
        timeout = 7000
        with open(FLOORC_PATH, 'wb') as floorc_fd:
            floorc_fd.write(INITIAL_FLOORC.encode('utf-8'))

    def open_floorc(active_window):
        active_window.open_file(FLOORC_PATH)
        utils.set_timeout(webbrowser.open, timeout, 'https://floobits.com/dash/initial_floorc', new=2, autoraise=True)

    get_active_window(open_floorc)


if not (G.USERNAME and G.SECRET):
    initial_run()

DATA = utils.get_persistent_data()


def global_tick():
    Listener.push()
    if G.AGENT and G.AGENT.sock:
        G.AGENT.select()
    utils.set_timeout(global_tick, G.TICK_TIME)


def disconnect_dialog():
    if G.AGENT and G.CONNECTED:
        disconnect = bool(sublime.ok_cancel_dialog('You can only be in one workspace at a time. Leave the current workspace?'))
        if disconnect:
            msg.debug('Stopping agent.')
            G.AGENT.stop()
            G.AGENT = None
        return disconnect
    return True


class FloobitsBaseCommand(sublime_plugin.WindowCommand):
    def is_visible(self):
        return bool(self.is_enabled())

    def is_enabled(self):
        return bool(G.AGENT and G.AGENT.is_ready())


class FloobitsShareDirCommand(sublime_plugin.WindowCommand):
    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def run(self, dir_to_share='', paths=None, current_file=False):
        reload_settings()
        if not (G.USERNAME and G.SECRET):
            return initial_run()
        if paths:
            if len(paths) != 1:
                return sublime.error_message('Only one folder at a time, please. :(')
            return self.on_input(paths[0])
        self.window.show_input_panel('Directory to share:', dir_to_share, self.on_input, None, None)

    def on_input(self, dir_to_share):
        global ON_CONNECT
        dir_to_share = os.path.expanduser(dir_to_share)
        dir_to_share = os.path.realpath(utils.unfuck_path(dir_to_share))
        workspace_name = os.path.basename(dir_to_share)
        floo_workspace_dir = os.path.join(G.COLAB_DIR, G.USERNAME, workspace_name)
        print(G.COLAB_DIR, G.USERNAME, workspace_name, floo_workspace_dir)

        if os.path.isfile(dir_to_share):
            return sublime.error_message('Give me a directory please')

        try:
            utils.mkdir(dir_to_share)
        except Exception:
            return sublime.error_message("The directory %s doesn't exist and I can't make it." % dir_to_share)

        floo_file = os.path.join(dir_to_share, '.floo')

        info = {}
        try:
            floo_info = open(floo_file, 'rb').read().decode('utf-8')
            info = json.loads(floo_info)
        except (IOError, OSError):
            pass
        except Exception:
            print("Couldn't read the floo_info file: %s" % floo_file)

        workspace_url = info.get('url')
        if workspace_url:
            try:
                result = utils.parse_url(workspace_url)
            except Exception as e:
                sublime.error_message(str(e))
                workspace_url = None
            else:
                workspace_name = result['workspace']

                # floo_workspace_dir = os.path.join(G.COLAB_DIR, result['owner'], result['workspace'])
        for owner, workspaces in DATA['workspaces']:
            for name, workspace in workspaces:
                if workspace['path'] == dir_to_share:
                    workspace_url = workspace['url']
                    break

        if workspace_url:
            ON_CONNECT = lambda x: Listener.create_buf(dir_to_share)
            return self.window.run_command('floobits_join_workspace', {'workspace_url': workspace_url})

        # make & join workspace
        ON_CONNECT = lambda x: Listener.create_buf(dir_to_share)
        self.window.run_command('floobits_create_workspace', {
            'workspace_name': workspace_name,
            'dir_to_share': dir_to_share,
        })


class FloobitsCreateWorkspaceCommand(sublime_plugin.WindowCommand):
    def is_visible(self):
        return False

    def is_enabled(self):
        return True

    def run(self, workspace_name='', dir_to_share='', prompt='Workspace name:'):
        if not disconnect_dialog():
            return
        if ssl is False:
            return sublime.error_message('Your version of Sublime Text can\'t create workspaces because it has a broken SSL module. This is a known issue on Linux and Windows builds of Sublime Text 2. Please upgrade to Sublime Text 3. See http://sublimetext.userecho.com/topic/50801-bundle-python-ssl-module/ for more information.')
        self.owner = G.USERNAME
        self.dir_to_share = dir_to_share
        self.window.show_input_panel(prompt, workspace_name, self.on_input, None, None)

    def on_input(self, workspace_name):
        if workspace_name == '':
            return self.run(dir_to_share=self.dir_to_share)
        try:
            api.create_workspace(workspace_name)
            workspace_url = 'https://%s/r/%s/%s' % (G.DEFAULT_HOST, G.USERNAME, workspace_name)
            print('Created workspace %s' % workspace_url)
        except HTTPError as e:
            if e.code not in [400, 409]:
                return sublime.error_message('Unable to create workspace: %s' % unicode(e))

            if e.code == 400:
                workspace_name = ''.join(workspace_name.split())
                args = {
                    'dir_to_share': self.dir_to_share,
                    'workspace_name': workspace_name,
                    'prompt': 'Invalid name. Workspace names must match the regex [A-Za-z0-9_\-]. Choose another name:'
                }
                return self.window.run_command('floobits_create_workspace', args)

            args = {
                'dir_to_share': self.dir_to_share,
                'workspace_name': workspace_name,
                'prompt': 'Workspace %s already exists. Choose another name:' % workspace_name
            }

            return self.window.run_command('floobits_create_workspace', args)
        except Exception as e:
            return sublime.error_message('Unable to create workspace: %s' % unicode(e))

        d = utils.get_persistent_data()
        d['workspaces'][self.owner]
        new_path = os.path.join(os.path.dirname(self.ln_path), workspace_name)
        if self.ln_path and self.ln_path != new_path:
            try:
                os.rename(self.ln_path, new_path)
            except Exception as e:
                return sublime.error_message('os.rename(%s, %s) failed after creating workspace: %s' % (self.ln_path, new_path, str(e)))

        webbrowser.open(workspace_url + '/settings', new=2, autoraise=True)

        self.window.run_command('floobits_join_workspace', {
            'workspace_url': workspace_url,
        })


class FloobitsPromptJoinWorkspaceCommand(sublime_plugin.WindowCommand):

    def run(self, workspace='https://floobits.com/r/'):
        self.window.show_input_panel('Workspace URL:', workspace, self.on_input, None, None)

    def on_input(self, workspace_url):
        if disconnect_dialog():
            self.window.run_command('floobits_join_workspace', {
                'workspace_url': workspace_url,
            })


class FloobitsJoinWorkspaceCommand(sublime_plugin.WindowCommand):

    def run(self, workspace_url):
        def open_workspace_window2(cb):
            if sublime.platform() == 'linux':
                subl = open('/proc/self/cmdline').read().split(chr(0))[0]
            elif sublime.platform() == 'osx':
                # TODO: totally explodes if you install ST2 somewhere else
                subl = settings.get('sublime_executable', '/Applications/Sublime Text 2.app/Contents/SharedSupport/bin/subl')
                if not os.path.exists(subl):
                    return sublime.error_message('Can\'t find your Sublime Text executable at %s. Please add "sublime_executable /path/to/subl" to your ~/.floorc and restart Sublime Text' % subl)
            elif sublime.platform() == 'windows':
                subl = sys.executable
            else:
                raise Exception('WHAT PLATFORM ARE WE ON?!?!?')

            command = [subl]
            if utils.get_workspace_window() is None:
                command.append('--new-window')
            command.append('--add')
            command.append(G.PROJECT_PATH)

            # Maybe no msg view yet :(
            print('command:', command)
            p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            poll_result = p.poll()
            print('poll:', poll_result)

            def truncate_chat_view(chat_view):
                if chat_view:
                    chat_view.set_read_only(False)
                    chat_view.run_command('floo_view_replace_region', {'r': [0, chat_view.size()], 'data': ''})
                    chat_view.set_read_only(True)
                cb()

            def create_chat_view():
                with open(os.path.join(G.BASE_DIR, 'msgs.floobits.log'), 'a') as msgs_fd:
                    msgs_fd.write('')
                msg.get_or_create_chat(truncate_chat_view)
            utils.set_workspace_window(create_chat_view)

        def open_workspace_window3(cb):
            G.WORKSPACE_WINDOW = utils.get_workspace_window()
            if not G.WORKSPACE_WINDOW:
                G.WORKSPACE_WINDOW = sublime.active_window()
            msg.debug('Setting project data. Path: %s' % G.PROJECT_PATH)
            G.WORKSPACE_WINDOW.set_project_data({'folders': [{'path': G.PROJECT_PATH}]})

            def truncate_chat_view(chat_view):
                if chat_view:
                    chat_view.set_read_only(False)
                    chat_view.run_command('floo_view_replace_region', {'r': [0, chat_view.size()], 'data': ''})
                    chat_view.set_read_only(True)
                cb()

            with open(os.path.join(G.BASE_DIR, 'msgs.floobits.log'), 'a') as msgs_fd:
                msgs_fd.write('')
            msg.get_or_create_chat(truncate_chat_view)

        def open_workspace_window(cb):
            if PY2:
                open_workspace_window2(cb)
            else:
                open_workspace_window3(cb)

        def run_agent(owner, workspace, host, port, secure):
            if G.AGENT:
                msg.debug('Stopping agent.')
                G.AGENT.stop()
                G.AGENT = None
            try:
                G.AGENT = AgentConnection(owner, workspace, host=host, port=port, secure=secure, on_connect=ON_CONNECT)
                # owner and workspace name are slugfields so this should be safe
                Listener.reset()
                G.AGENT.connect()
            except Exception as e:
                print(e)
                tb = traceback.format_exc()
                print(tb)
            else:
                joined_workspace = {'url': workspace_url}
                update_recent_workspaces(joined_workspace)

        def run_thread(*args):
            run_agent(**result)

        def link_dir(d):
            if d == '' or d.find(G.PROJECT_PATH) == 0:
                try:
                    utils.mkdir(d)
                except Exception as e:
                    return sublime.error_message("Couldn't create directory %s: %s" % (d, str(e)))
                return open_workspace_window(run_thread)

            try:
                utils.mkdir(os.path.dirname(G.PROJECT_PATH))
            except Exception as e:
                return sublime.error_message("Couldn't create directory %s: %s" % (os.path.dirname(G.PROJECT_PATH), str(e)))

            d = os.path.realpath(os.path.expanduser(d))
            if not os.path.isdir(d):
                make_dir = sublime.ok_cancel_dialog('%s is not a directory. Create it?' % d)
                if not make_dir:
                    return self.window.show_input_panel('%s is not a directory. Enter an existing path:' % d, d, link_dir, None, None)
                try:
                    utils.mkdir(d)
                except Exception as e:
                    return sublime.error_message("Could not create directory %s: %s" % (d, str(e)))
            try:
                os.symlink(d, G.PROJECT_PATH)
            except Exception as e:
                return sublime.error_message("Couldn't create symlink from %s to %s: %s" % (d, G.PROJECT_PATH, str(e)))

            open_workspace_window(run_thread)

        try:
            result = utils.parse_url(workspace_url)
        except Exception as e:
            return sublime.error_message(str(e))
        reload_settings()
        if not (G.USERNAME and G.SECRET):
            return initial_run()
        G.PROJECT_PATH = os.path.realpath(os.path.join(G.COLAB_DIR, result['owner'], result['workspace']))
        print('Project path is %s' % G.PROJECT_PATH)
        if not os.path.isdir(G.PROJECT_PATH):
            # mediocre prompt here
            return self.window.show_input_panel('Give me a directory to sync data into:', G.PROJECT_PATH, link_dir, None, None)

        open_workspace_window(run_thread)


class FloobitsLeaveWorkspaceCommand(FloobitsBaseCommand):

    def run(self):
        if G.AGENT:
            G.AGENT.stop()
            G.AGENT = None
            # TODO: Mention the name of the thing we left
            sublime.error_message('You have left the workspace.')
        else:
            sublime.error_message('You are not joined to any workspace.')


class FloobitsRejoinWorkspaceCommand(FloobitsBaseCommand):

    def run(self):
        if G.AGENT:
            workspace_url = utils.to_workspace_url({
                'host': G.AGENT.host,
                'owner': G.AGENT.owner,
                'port': G.AGENT.port,
                'workspace': G.AGENT.workspace,
                'secure': G.AGENT.secure,
            })
            G.AGENT.stop()
            G.AGENT = None
        else:
            try:
                workspace_url = DATA['recent_workspaces'][0]['url']
            except Exception:
                sublime.error_message('No recent workspace to rejoin.')
                return
        self.window.run_command('floobits_join_workspace', {
            'workspace_url': workspace_url,
        })

    def is_visible(self):
        return bool(self.is_enabled())

    def is_enabled(self):
        return True


class FloobitsPromptMsgCommand(FloobitsBaseCommand):

    def run(self, msg=''):
        print(('msg', msg))
        self.window.show_input_panel('msg:', msg, self.on_input, None, None)

    def on_input(self, msg):
        self.window.run_command('floobits_msg', {'msg': msg})


class FloobitsMsgCommand(FloobitsBaseCommand):
    def run(self, msg):
        if not msg:
            return
        if G.AGENT:
            G.AGENT.send_msg(msg)

    def description(self):
        return 'Send a message to the floobits workspace you are in (join a workspace first)'


class FloobitsClearHighlightsCommand(FloobitsBaseCommand):
    def run(self):
        Listener.clear_highlights(self.window.active_view())


class FloobitsSummonCommand(FloobitsBaseCommand):
    # TODO: ghost this option if user doesn't have permissions
    def run(self):
        Listener.summon(self.window.active_view())


class FloobitsJoinRecentWorkspaceCommand(sublime_plugin.WindowCommand):
    def _get_recent_workspaces(self):
        recent_workspaces = []
        if 'recent_workspaces' not in DATA:
            # TODO: remove this when everyone has had plenty of time to update the plugin
            DATA['recent_workspaces'] = DATA.get('recent_rooms', [])

        try:
            recent_workspaces = [x.get('url') for x in DATA['recent_workspaces'] if x.get('url') is not None]
        except Exception:
            pass
        return recent_workspaces

    def run(self, *args):
        workspaces = self._get_recent_workspaces()
        self.window.show_quick_panel(workspaces, self.on_done)

    def on_done(self, item):
        if item == -1:
            return
        workspace = DATA['recent_workspaces'][item]
        if disconnect_dialog():
            self.window.run_command('floobits_join_workspace', {'workspace_url': workspace['url']})

    def is_enabled(self):
        return bool(len(self._get_recent_workspaces()) > 0)


class FloobitsOpenMessageViewCommand(FloobitsBaseCommand):
    def run(self, *args):
        def print_msg(chat_view):
            msg.log('Opened message view')
            if not G.AGENT:
                msg.log('Not joined to a workspace.')

        msg.get_or_create_chat(print_msg)

    def description(self):
        return 'Open the floobits messages view.'


class FloobitsAddToWorkspaceCommand(FloobitsBaseCommand):
    def run(self, paths, current_file=False):
        if not self.is_enabled():
            return

        if paths is None and current_file:
            paths = [self.window.active_view().file_name()]

        for path in paths:
            Listener.create_buf(path)

    def description(self):
        return 'Add file or directory to currently-joined Floobits workspace.'


class FloobitsDeleteFromWorkspaceCommand(FloobitsBaseCommand):
    def run(self, paths, current_file=False):
        if not self.is_enabled():
            return

        confirm = bool(sublime.ok_cancel_dialog('This will delete your local copy as well. Are you sure you want do do this?'))
        if not confirm:
            return

        if paths is None and current_file:
            paths = [self.window.active_view().file_name()]

        for path in paths:
            Listener.delete_buf(path)

    def description(self):
        return 'Add file or directory to currently-joined Floobits workspace.'


class FloobitsCreateHangoutCommand(FloobitsBaseCommand):
    def run(self):
        owner = G.AGENT.owner
        workspace = G.AGENT.workspace
        webbrowser.open('https://plus.google.com/hangouts/_?gid=770015849706&gd=%s/%s' % (owner, workspace))

    def is_enabled(self):
        return bool(G.AGENT and G.AGENT.is_ready() and G.AGENT.owner and G.AGENT.workspace)


class FloobitsPromptHangoutCommand(sublime_plugin.WindowCommand):
    def run(self, hangout_url):
        confirm = bool(sublime.ok_cancel_dialog('This workspace is being edited in a Google+ Hangout? Do you want to join the hangout?'))
        if not confirm:
            return
        webbrowser.open(hangout_url)

    def is_visible(self):
        return False

    def is_enabled(self):
        return bool(G.AGENT and G.AGENT.is_ready() and G.AGENT.owner and G.AGENT.workspace)


class FloobitsHelpCommand(FloobitsBaseCommand):
    def run(self):
        webbrowser.open('https://floobits.com/help/plugins/#sublime-usage', new=2, autoraise=True)

    def is_visible(self):
        return True

    def is_enabled(self):
        return True


class FloobitsEnableStalkerModeCommand(FloobitsBaseCommand):
    def run(self):
        G.STALKER_MODE = True
        # TODO: go to most recent highlight

    def is_visible(self):
        return bool(self.is_enabled())

    def is_enabled(self):
        return bool(G.AGENT and G.AGENT.is_ready() and not G.STALKER_MODE)


class FloobitsDisableStalkerModeCommand(FloobitsBaseCommand):
    def run(self):
        G.STALKER_MODE = False

    def is_visible(self):
        return bool(self.is_enabled())

    def is_enabled(self):
        return bool(G.AGENT and G.AGENT.is_ready() and G.STALKER_MODE)


class FloobitsNotACommand(sublime_plugin.WindowCommand):
    def run(self, *args, **kwargs):
        pass

    def is_visible(self):
        return True

    def is_enabled(self):
        return False

    def description(self):
        return


# The new ST3 plugin API sucks
class FlooViewSetMsg(sublime_plugin.TextCommand):
    def run(self, edit, data, *args, **kwargs):
        size = self.view.size()
        self.view.set_read_only(False)
        self.view.insert(edit, size, data)
        self.view.set_read_only(True)
        # TODO: this scrolling is lame and centers text :/
        self.view.show(size)

    def is_visible(self):
        return False

    def is_enabled(self):
        return True

    def description(self):
        return


ignore_modified_timeout = None


def unignore_modified_events():
    G.IGNORE_MODIFIED_EVENTS = False


def transform_selections(selections, start, new_offset):
    new_sels = []
    for sel in selections:
        a = sel.a
        b = sel.b
        if sel.a > start:
            a += new_offset
        if sel.b > start:
            b += new_offset
        new_sels.append(sublime.Region(a, b))
    return new_sels


# The new ST3 plugin API sucks
class FlooViewReplaceRegion(sublime_plugin.TextCommand):
    def run(self, edit, *args, **kwargs):
        selections = [x for x in self.view.sel()]  # deep copy
        selections = self._run(edit, selections, *args, **kwargs)
        self.view.sel().clear()
        for sel in selections:
            self.view.sel().add(sel)

    def _run(self, edit, selections, r, data):
        global ignore_modified_timeout

        if not getattr(self, 'view', None):
            return selections

        G.IGNORE_MODIFIED_EVENTS = True
        utils.cancel_timeout(ignore_modified_timeout)
        ignore_modified_timeout = utils.set_timeout(unignore_modified_events, 2)
        start = max(int(r[0]), 0)
        stop = min(int(r[1]), self.view.size())
        region = sublime.Region(start, stop)

        if stop - start > 10000:
            self.view.replace(edit, region, data)
            G.VIEW_TO_HASH[self.view.buffer_id()] = hashlib.md5(listener.get_text(self.view).encode('utf-8')).hexdigest()
            return transform_selections(selections, start, stop - start)

        existing = self.view.substr(region)
        i = 0
        data_len = len(data)
        existing_len = len(existing)
        length = min(data_len, existing_len)
        while (i < length):
            if existing[i] != data[i]:
                break
            i += 1
        j = 0
        while j < (length - i):
            if existing[existing_len - j - 1] != data[data_len - j - 1]:
                break
            j += 1
        region = sublime.Region(start + i, stop - j)
        replace_str = data[i:data_len - j]
        self.view.replace(edit, region, replace_str)
        G.VIEW_TO_HASH[self.view.buffer_id()] = hashlib.md5(listener.get_text(self.view).encode('utf-8')).hexdigest()
        new_offset = len(replace_str) - ((stop - j) - (start + i))
        return transform_selections(selections, start + i, new_offset)

    def is_visible(self):
        return False

    def is_enabled(self):
        return True

    def description(self):
        return


# The new ST3 plugin API sucks
class FlooViewReplaceRegions(FlooViewReplaceRegion):
    def run(self, edit, commands):
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        selections = [x for x in self.view.sel()]  # deep copy
        for command in commands:
            selections = self._run(edit, selections, **command)

        self.view.set_read_only(is_read_only)
        self.view.sel().clear()
        for sel in selections:
            self.view.sel().add(sel)

    def is_visible(self):
        return False

    def is_enabled(self):
        return True

    def description(self):
        return


global_tick()
