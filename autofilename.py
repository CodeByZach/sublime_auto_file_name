import sublime
import sublime_plugin
import re
import os
import ctypes
import platform
import itertools
import string
import time
from .getimageinfo import getImageInfo

g_auto_completions = []
MAXIMUM_WAIT_TIME = 0.3

def msg(msg):
	print("[AutoFileName] %s" % msg)

def debug(msg):
	verbose_debugging = sublime.load_settings('autofilename.sublime-settings').get('afn_console_debugging')
	if verbose_debugging == True:
		print("[AutoFileName][DEBUG] %s" % msg)




class AfnShowFilenames(sublime_plugin.TextCommand):
	def run(self, edit):
		FileNameComplete.is_active = True
		self.view.run_command('auto_complete', {'disable_auto_insert': True, 'next_completion_if_showing': False})



class AfnSettingsPanel(sublime_plugin.WindowCommand):
	def run(self):
		use_pr = '✗ Stop using project root' if self.get_setting('afn_use_project_root') else '✓ Use Project Root'
		use_dim = '✗ Disable HTML Image Dimension insertion' if self.get_setting('afn_insert_dimensions') else '✓ Auto-insert Image Dimensions in HTML'
		p_root = self.get_setting('afn_proj_root')

		menu = [
			[use_pr, p_root],
			[use_dim, '<img src="_path_" width = "x" height = "y" >']
		]
		self.window.show_quick_panel(menu, self.on_done)


	def on_done(self, value):
		settings = sublime.load_settings('autofilename.sublime-settings')
		if value == 0:
			use_pr = settings.get('afn_use_project_root')
			settings.set('afn_use_project_root', not use_pr)
		if value == 1:
			use_dim = settings.get('afn_use_project_root')
			settings.set('afn_use_project_root', not use_dim)


	def get_setting(self, string, view=None):
		if view and view.settings().get(string):
			return view.settings().get(string)
		else:
			return sublime.load_settings('autofilename.sublime-settings').get(string)



# Used to remove the / or \ when autocompleting a Windows drive (eg. /C:/path).
class AfnDeletePrefixedSlash(sublime_plugin.TextCommand):
	def run(self, edit):
		selection = getSelection(view)
		if selection != False:
			debug("AfnDeletePrefixedSlash")
			selectionStart = selection.a
			length = 5 if (self.view.substr(sublime.Region(selectionStart-5,selectionStart-3)) == '\\\\') else 4
			reg = sublime.Region(selectionStart-length,selectionStart-3)
			self.view.erase(edit, reg)



# Inserts width and height dimensions into img tags. HTML only.
class InsertDimensionsCommand(sublime_plugin.TextCommand):
	this_dir = ''

	def insert_dimension(self, edit, dim, name, tag_scope):
		view = self.view
		selection = getSelection(view)
		if selection != False:
			selectionStart = selection.a

			if name in view.substr(tag_scope):
				reg = view.find('(?<='+name+'\=)\s*\"\d{1,5}', tag_scope.a)
				view.replace(edit, reg, '"'+str(dim))
			else:
				dimension = str(dim)
				view.insert(edit, selectionStart+1, ' '+name+'="'+dimension+'"')


	def get_setting(self, string, view=None):
		if view and view.settings().get(string):
			return view.settings().get(string)
		else:
			return sublime.load_settings('autofilename.sublime-settings').get(string)


	def insert_dimensions(self, edit, scope, w, h):
		view = self.view

		if self.get_setting('afn_insert_width_first',view):
			self.insert_dimension(edit,h,'height', scope)
			self.insert_dimension(edit,w,'width', scope)
		else:
			self.insert_dimension(edit,w,'width', scope)
			self.insert_dimension(edit,h,'height', scope)


	# Determines if there is a template tag in a given region. Supports HTML and template languages.
	def img_tag_in_region(self, region):
		view = self.view

		# Handle template languages but template languages like slim may also contain HTML so we do a check for that as well.
		return view.substr(region).strip().startswith('img') | ('<img' in view.substr(region))


	def run(self, edit):
		view = self.view
		view.run_command("commit_completion")
		selection = getSelection(view)
		if selection != False:
			selectionStart = selection.a

			if not 'html' in view.scope_name(selectionStart): return
			scope = view.extract_scope(selectionStart-1)

			# If using a template language, the scope is set to the current line.
			tag_scope = view.line(selectionStart) if self.get_setting('afn_template_languages',view) else view.extract_scope(scope.a-1)

			path = view.substr(scope)
			if path.startswith(("'","\"","(")):
				path = path[1:-1]

			path = path[path.rfind(FileNameComplete.sep):] if FileNameComplete.sep in path else path
			full_path = self.this_dir + path

			if self.img_tag_in_region(tag_scope) and path.endswith(('.png','.jpg','.jpeg','.gif')):
				with open(full_path,'rb') as r:
					read_data = r.read() if path.endswith(('.jpg','.jpeg')) else r.read(24)
				w, h = getImageInfo(read_data)

				self.insert_dimensions(edit, tag_scope, w, h)



# When backspacing through a path, selects the previous path component.
class ReloadAutoCompleteCommand(sublime_plugin.TextCommand):
	def run(self, edit):
		view = self.view
		selection = getSelection(view)
		if selection != False:
			if not allow_to_continue(view, selection):
				return
			debug("ReloadAutoCompleteCommand")
			view.run_command('hide_auto_complete')
			view.run_command('left_delete')

			selectionStart = selection.a
			scope = view.extract_scope(selectionStart-1)
			scope_text = view.substr(scope)
			slash_pos = scope_text[:selectionStart - scope.a].rfind(FileNameComplete.sep)
			slash_pos += 1 if slash_pos < 0 else 0
			region = sublime.Region(scope.a+slash_pos+1,selectionStart-1)
			view.sel().add(region)



def enable_autocomplete():
	"""
		Used externally by other packages which want to autocomplete file paths
	"""
	debug("enable_autocomplete")
	FileNameComplete.is_forced = True



def disable_autocomplete():
	"""
		Used externally by other packages which want to autocomplete file paths
	"""
	debug("disable_autocomplete")
	FileNameComplete.is_forced = False



def getSelection(view):
	selections = view.sel()
	if len(selections) > 0:
		return selections[0]
	else:
		return False



def in_supported_tag(view, selection):
	useValidTags = sublime.load_settings('autofilename.sublime-settings').get('afn_use_valid_tags')
	validTags = sublime.load_settings('autofilename.sublime-settings').get('afn_valid_tags')

	# Short-circuit and return true if list is empty.
	if not useValidTags:
		return True

	tagRegex = '(\w+)(?:(?=\W)\S)+$'
	viewContent = view.substr(sublime.Region(0, view.extract_scope(selection.a).a))

	matches = re.search(tagRegex, viewContent, re.IGNORECASE)
	if matches is not None:
		return matches.group(1) in validTags
	else:
		return False



def allow_to_continue(view, selection):
	if not in_supported_tag(view, selection):
		if not sublime.load_settings('autofilename.sublime-settings').get('afn_use_keybinding'):
			return False
		# return False
	return True



class FileNameComplete(sublime_plugin.EventListener):
	def __init__(self):
		debug("__init__")
		FileNameComplete.is_forced = False
		FileNameComplete.is_active = False


	def on_activated(self, view):
		debug("on_activated")
		self.showing_win_drives = False
		FileNameComplete.is_active = False
		FileNameComplete.sep = '/'


####################################################################### BEFORE #######################################################################
	# def get_drives(self):
	# 	# Search through valid drive names and see if they exist. (stolen from Facelessuser)
	# 	return [[d+":"+FileNameComplete.sep, d+":"+FileNameComplete.sep] for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(d + ":")]
#-----------------------------------------------------------------------------------------------------------------------------------------------------
	def get_drives(self):
		if 'Windows' not in platform.system():
			return []

		drive_bitmask = ctypes.cdll.kernel32.GetLogicalDrives()
		drive_list = list(itertools.compress(string.ascii_uppercase,
			map(lambda x:ord(x) - ord('0'), bin(drive_bitmask)[:1:-1])))

		# Overrides default auto completion
		# https://github.com/BoundInCode/AutoFileName/issues/18
		for driver in drive_list:
			g_auto_completions.append( driver + ":" + FileNameComplete.sep )

			if time.time() - self.start_time > MAXIMUM_WAIT_TIME:
				return
####################################################################### AFTER ########################################################################


	def on_query_context(self, view, key, operator, operand, match_all):
		selection = getSelection(view)
		if selection != False:
			if not allow_to_continue(view, selection):
				return
			debug("on_query_context")
			if key == "afn_deleting_slash":  # for reloading autocomplete
				valid = self.at_path_end(view) and selection.empty() and view.substr(selection.a-1) == FileNameComplete.sep
				return valid == operand


	def at_path_end(self, view):
		selection = getSelection(view)
		if selection != False:
			name = view.scope_name(selection.a)
			if selection.empty() and ('string.end' in name or 'string.quoted.end.js' in name):
				return True
			if '.css' in name and view.substr(selection.a) == ')':
				return True
			return False


####################################################################### BEFORE #######################################################################
	# def on_modified(self, view):
#-----------------------------------------------------------------------------------------------------------------------------------------------------
	def on_modified_async(self, view):
####################################################################### AFTER ########################################################################
		selection = getSelection(view)
		if selection != False:
			if not allow_to_continue(view, selection):
				return
			debug("on_modified_async")
			selectionStart = selection.a
			txt = view.substr(sublime.Region(selectionStart-4,selectionStart-3))
			if (self.showing_win_drives and txt == FileNameComplete.sep):
				self.showing_win_drives = False
				view.run_command('afn_delete_prefixed_slash')


	def on_selection_modified_async(self, view):
		if not view.window():
			return

		# Fix sublime.py, line 641, in __getitem__ raise IndexError()
		selection = getSelection(view)
		if selection != False:
			FileNameComplete.is_active = in_supported_tag(view, selection)
		else:
			return

		# Open autocomplete automatically if active or forced
		if not FileNameComplete.is_forced and not FileNameComplete.is_active:
			return
		debug("on_selection_modified_async")
		# view_name = view.name()
		# buffer_id = view.buffer_id()

		if selection.empty():
			# Check if keybinding mode is not enabled, and we are at the end of the path before continuing.
			if not self.get_setting('afn_use_keybinding', view) and self.get_setting('afn_automatic_dropdown_only_eop', view) and not self.at_path_end(view):
				return

			file_name = view.file_name()
			scope_contents = view.substr(view.extract_scope(selection.a-1))
			extracted_path = scope_contents.replace('\r\n', '\n').split('\n')[0]

			if('\\' in extracted_path and not '/' in extracted_path):
				FileNameComplete.sep = '\\'
			else:
				FileNameComplete.sep = '/'
			if view.substr(selection.a-1) == FileNameComplete.sep or len(view.extract_scope(selection.a)) < 3 or not file_name:
				view.run_command('auto_complete', {'disable_auto_insert': True, 'next_completion_if_showing': False})
				FileNameComplete.is_active = False
		else:
			FileNameComplete.is_active = False


	def fix_dir(self, sdir, fn):
		if fn.endswith(('.png','.jpg','.jpeg','.gif')):
			path = os.path.join(sdir, fn)
			with open(path,'rb') as r:
				read_data = r.read() if path.endswith(('.jpg','.jpeg')) else r.read(24)
			w, h = getImageInfo(read_data)
			return fn+'\t'+'w:'+ str(w) +" h:" + str(h)
####################################################################### BEFORE #######################################################################
		# return fn
#-----------------------------------------------------------------------------------------------------------------------------------------------------
		# Overrides default auto completion, replaces dot `.` by a `ꓸ` (Lisu Letter Tone Mya Ti)
		# https://github.com/BoundInCode/AutoFileName/issues/18
		return fn.replace(".", "ꓸ")
####################################################################### AFTER ########################################################################


	def get_cur_path(self, view, selection):
		scope_contents = view.substr(view.extract_scope(selection-1)).strip()
		cur_path = scope_contents.replace('\r\n', '\n').split('\n')[0]
		if cur_path.startswith(("'","\"","(")):
			cur_path = cur_path[1:-1]

		return cur_path[:cur_path.rfind(FileNameComplete.sep)+1] if FileNameComplete.sep in cur_path else ''


	def get_setting(self, string, view=None):
		if view and view.settings().get(string):
			return view.settings().get(string)
		else:
			return sublime.load_settings('autofilename.sublime-settings').get(string)


	def on_query_completions(self, view, prefix, locations):
		if not (FileNameComplete.is_forced or FileNameComplete.is_active):
			return
		debug("on_query_completions")
		selection = getSelection(view)
		if selection != False:
			selectionStart = selection.a
		else:
			return

		if "string.regexp.js" in view.scope_name(selectionStart):
			return []
		blacklist = self.get_setting('afn_blacklist_scopes', view)
		valid_scopes = self.get_setting('afn_valid_scopes',view)

		if not any(scope in view.scope_name(selectionStart) for scope in valid_scopes):
			return
		if blacklist and any(scope in view.scope_name(selectionStart) for scope in blacklist):
			return

		self.view = view
		self.selection = selectionStart
		self.start_time = time.time()
		self.get_completions()

		return g_auto_completions


	def get_completions(self):
		g_auto_completions.clear()
		file_name = self.view.file_name()
		is_proj_rel = self.get_setting('afn_use_project_root',self.view)
		this_dir = ""
		cur_path = os.path.expanduser(self.get_cur_path(self.view, self.selection))

		if cur_path.startswith('\\\\') and not cur_path.startswith('\\\\\\') and sublime.platform() == "windows":
			self.showing_win_drives = True
			self.get_drives()
			return
		elif cur_path.startswith('/') or cur_path.startswith('\\'):
			if is_proj_rel and file_name:
				proot_list = self.get_setting('afn_proj_root', self.view)
				proot = ""

				if proot_list:
					for proot_path in proot_list:
						if os.path.isabs(proot_path):
							proot = proot_path
							continue

					cur_path = os.path.join(proot, cur_path[1:])

				for f in sublime.active_window().folders():
					if f in file_name:
						this_dir = os.path.join(f, cur_path.lstrip('/').lstrip('\\'))
		elif not file_name:
			this_dir = cur_path
		else:
			this_dir = os.path.split(file_name)[0]
			this_dir = os.path.join(this_dir, cur_path)

		try:
			if os.path.isabs(cur_path) and (not is_proj_rel or not this_dir):
				if sublime.platform() == "windows" and len(self.view.extract_scope(self.selection)) < 4:
					self.showing_win_drives = True
					self.get_drives()
					return
				elif sublime.platform() != "windows":
					this_dir = cur_path

			self.showing_win_drives = False
			dir_files = os.listdir(this_dir)

			for directory in dir_files:
				if directory.startswith( '.' ): continue
				if not '.' in directory: directory += FileNameComplete.sep

				g_auto_completions.append( ( self.fix_dir( this_dir,directory ), directory ) )
				InsertDimensionsCommand.this_dir = this_dir

				if time.time() - self.start_time > MAXIMUM_WAIT_TIME:
					return

		except OSError:
			debug("get_completions, AutoFileName: could not find " + this_dir)
			pass
