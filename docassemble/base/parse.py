import re
import ast
import yaml
import os
import os.path
import sys
import httplib2
import datetime
import operator
import pprint
import docassemble.base.filter
from docassemble.base.error import DAError, MandatoryQuestion
from docassemble.base.util import pickleable_objects, word, get_language
from docassemble.base.logger import logmessage
from docassemble.base.pandoc import Pandoc
from mako.template import Template
from types import CodeType

debug = False
match_mako = re.compile(r'<%|\${|% if|% for|% while')
emoji_match = re.compile(r':([^ ]+):')
nameerror_match = re.compile(r'\'(.*)\' is not defined')
document_match = re.compile(r'^---$', flags=re.MULTILINE)
remove_trailing_dots = re.compile(r'\.\.\.$')
dot_split = re.compile(r'([^\.\[\]]+(?:\[.*?\])?)')
match_brackets_at_end = re.compile(r'^(.*)(\[[^\[]+\])$')
match_inside_brackets = re.compile(r'\[([^\]+])\]')

def textify(data):
    return list(map((lambda x: x.text(user_dict)), data))
    
def set_url_finder(func):
    docassemble.base.filter.set_url_finder(func)
    docassemble.base.util.set_url_finder(func)

def set_file_finder(func):
    docassemble.base.filter.set_file_finder(func)

def set_mail_variable(func):
    docassemble.base.filter.set_mail_variable(func)

def blank_save_numbered_file(*args, **kwargs):
    return(None, None, None)

save_numbered_file = blank_save_numbered_file

def set_save_numbered_file(func):
    global save_numbered_file
    #logmessage("set the save_numbered_file function to " + str(func))
    save_numbered_file = func
    return

class PackageImage(object):
    def __init__(self, **kwargs):
        self.filename = kwargs.get('filename', None)
        self.attribution = kwargs.get('attribution', None)
        self.setname = kwargs.get('setname', None)
        self.package = kwargs.get('package', 'docassemble.base')
    def get_filename(self):
        return(docassemble.base.util.static_filename_path(str(self.package) + ':' + str(self.filename)))
    def get_reference(self):
        return str(self.package) + ':' + str(self.filename)

class InterviewSource(object):
    def __init__(self, **kwargs):
        self.language = '*'
        pass
    def set_path(self, path):
        self.path = path
        return
    def set_directory(self, directory):
        self.directory = directory
        return
    def set_content(self, content):
        self.content = content
        return
    def set_language(self, language):
        self.language = language
        return
    def update(self):
        return True
    def get_modtime(self):
        return self._modtime
    def get_language(self):
        return self.language
    def get_interview(self):
        return Interview(source=self)
    def append(self, path):
        return None

class InterviewSourceString(InterviewSource):
    def __init__(self, **kwargs):
        self.set_path(kwargs.get('path', None))
        self.set_directory(kwargs.get('directory', None))
        self.set_content(kwargs.get('content', None))
        return super(InterviewSourceString, self).__init__(**kwargs)

class InterviewSourceFile(InterviewSource):
    def __init__(self, **kwargs):
        if 'filepath' in kwargs:
            self.set_filepath(kwargs['filepath'])
        else:
            self.filepath = None
        if 'path' in kwargs:
            self.set_path(kwargs['path'])
        return super(InterviewSourceFile, self).__init__(**kwargs)
    def set_path(self, path):
        self.path = path
        parts = path.split(":")
        if len(parts) == 2:
            self.package = parts[0]
        else:
            self.package = None
        if self.filepath is None:
            self.set_filepath(interview_source_from_string(self.path))
        if self.package is None and re.search(r'docassemble.base.data.', self.filepath):
            self.package = 'docassemble.base'
        return
    def set_filepath(self, filepath):
        self.filepath = filepath
        if self.filepath is None:
            self.directory = None
        else:
            self.set_directory(os.path.dirname(self.filepath))
        return
    def update(self):
        try:
            with open(self.filepath) as the_file:
                self.set_content(the_file.read())
                return True
        except Exception as errmess:
            sys.stderr.write("Error:" + str(errmess) + "\n")
        return False
    def get_modtime(self):
        self._modtime = os.path.getmtime(self.filepath)
        return(self._modtime)
    def append(self, path):
        new_file = os.path.join(self.directory, path)
        if os.path.isfile(new_file) and os.access(new_file, os.R_OK):
            new_source = InterviewSourceFile()
            new_source.path = path
            new_source.filepath = new_file
            if new_source.update():
                return(new_source)
        return(None)
    
class InterviewSourceURL(InterviewSource):
    def __init__(self, **kwargs):
        self.set_path(kwargs.get('path', None))
        return super(InterviewSourceURL, self).__init__(**kwargs)
    def set_path(self, path):
        self.path = path
        if self.path is None:
            self.directory = None
        else:
            self.directory = re.sub('/[^/]*$', '', re.sub('\?.*', '', self.path))
        return
    def update(self):
        try:
            h = httplib2.Http()
            resp, content = h.request(self.path, "GET")
            if resp['status'] >= 200 and resp['status'] < 300:
                self.set_content(content)
                self._modtime = datetime.datetime.now()
                return True
        except:
            pass
        return False
    def append(self, path):
        new_file = os.path.join(self.directory, path)
        if os.path.isfile(new_file) and os.access(new_file, os.R_OK):
            new_source = InterviewSourceFile()
            new_source.path = path
            new_source.filepath = new_file
            if new_source.update():
                return(new_source)
        return None

class InterviewStatus(object):
    def __init__(self, current_info=dict(), **kwargs):
        self.current_info = current_info
        self.attributions = set()
        self.seeking = list()
        self.tracker = kwargs.get('tracker', -1)
        self.attachments = None
    def populate(self, question_result):
        self.question = question_result['question']
        self.questionText = question_result['question_text']
        self.subquestionText = question_result['subquestion_text']
        self.underText = question_result['under_text']
        self.decorations = question_result['decorations']
        self.helpText = question_result['help_text']
        self.attachments = question_result['attachments']
        self.selectcompute = question_result['selectcompute']
        self.defaults = question_result['defaults']
        self.hints = question_result['hints']
        self.helptexts = question_result['helptexts']
        self.extras = question_result['extras']
    def set_tracker(self, tracker):
        self.tracker = tracker

# def new_counter(initial_value=0):
#     d = {'counter': initial_value}
#     def f():
#         return_value = d['counter']
#         d['counter'] += 1
#         return(return_value)
#     return f

# increment_question_counter = new_counter()

class TextObject(object):
    def __init__(self, x):
        self.original_text = x
        if match_mako.search(x):
            self.template = Template(x, strict_undefined=True, input_encoding='utf-8')
            self.uses_mako = True
        else:
            self.uses_mako = False
    def text(self, user_dict):
        if self.uses_mako:
            return(self.template.render(**user_dict))
        else:
            return(self.original_text)
            
class Field:
    def __init__(self, data):
        if 'number' in data:
            self.number = data['number']
        else:
            self.number = 0
        if 'saveas' in data:
            self.saveas = data['saveas']
        if 'label' in data:
            self.label = data['label']
        if 'type' in data:
            self.datatype = data['type']
        if 'choicetype' in data:
            self.choicetype = data['choicetype']
        if 'default' in data:
            self.default = data['default']
        if 'hint' in data:
            self.hint = data['hint']
        if 'help' in data:
            self.helptext = data['help']
        if 'extras' in data:
            self.extras = data['extras']
        if 'selections' in data:
            self.selections = data['selections']
        if 'boolean' in data:
            self.datatype = 'boolean'
            self.sign = data['boolean']
        if 'choices' in data:
            self.fieldtype = 'multiple_choice'
            self.choices = data['choices']
        if 'has_code' in data:
            self.has_code = True
        # if 'script' in data:
        #     self.script = data['script']
        # if 'css' in data:
        #     self.css = data['css']
        if 'shuffle' in data:
            self.shuffle = data['shuffle']
        if 'required' in data:
            self.required = data['required']
        else:
            self.required = True

class Question:
    def idebug(self, data):
        return "\nIn file " + str(self.from_source.path) + " from package " + str(self.package) + ":\n" + yaml.dump(data)
    def __init__(self, data, caller, **kwargs):
        should_append = True
        if 'register_target' in kwargs:
            register_target = kwargs['register_target']
            main_list = False
        else:
            register_target = self
            main_list = True
        self.from_source = kwargs.get('source', None)
        self.package = kwargs.get('package', None)
        self.interview = caller
        if debug:
            self.source_code = kwargs.get('source_code', None)
        self.fields = []
        self.attachments = []
        self.name = None
        self.role = list()
        self.need = None
        self.helptext = None
        self.subcontent = None
        self.undertext = None
        self.progress = None
        self.script = None
        self.decorations = None
        self.allow_emailing = True
        self.fields_used = set()
        self.names_used = set()
        if 'default language' in data:
            self.from_source.set_language(data['default language'])
        if 'language' in data:
            self.language = data['language']
        else:
            self.language = self.from_source.get_language()
        if 'usedefs' in data:
            defs = list()
            if type(data['usedefs']) is list:
                usedefs = data['usedefs']
            else:
                usedefs = [data['usedefs']]
            for usedef in usedefs:
                if type(usedef) in [dict, list, bool]:
                    raise DAError("A usedefs section must consist of a list of strings or a single string." + self.idebug(data))
                if usedef not in self.interview.defs:
                    raise DAError('Referred to a non-existent def "' + usedef + '."  All defs must be defined before they are used.' + self.idebug(data))
                defs.extend(self.interview.defs[usedef])
            definitions = "\n".join(defs) + "\n";
        else:
            definitions = "";        
        if 'mandatory' in data and data['mandatory'] is True:
            self.is_mandatory = True
        else:
            self.is_mandatory = False
        if 'attachment options' in data:
            should_append = False
            if type(data['attachment options']) is not list:
                data['attachment options'] = [data['attachment options']]
            for attachment_option in data['attachment options']:
                if type(attachment_option) is not dict:
                    raise DAError("An attachment option must a dictionary." + self.idebug(data))
                for key in attachment_option:
                    value = attachment_option[key]
                    if key == 'initial yaml':
                        if 'initial_yaml' not in self.interview.attachment_options:
                            self.interview.attachment_options['initial_yaml'] = list()
                        if type(value) is list:
                            the_list = value
                        else:
                            the_list = [value]
                        for yaml_file in the_list:
                            if type(yaml_file) is not str:
                                raise DAError('An initial yaml file must be a string.' + self.idebug(data))
                            self.interview.attachment_options['initial_yaml'].append(docassemble.base.util.package_template_filename(yaml_file, package=self.package))
                    elif key == 'additional yaml':
                        if 'additional_yaml' not in self.interview.attachment_options:
                            self.interview.attachment_options['additional_yaml'] = list()
                        if type(value) is list:
                            the_list = value
                        else:
                            the_list = [value]
                        for yaml_file in the_list:
                            if type(yaml_file) is not str:
                                raise DAError('An additional yaml file must be a string.' + self.idebug(data))
                            self.interview.attachment_options['additional_yaml'].append(docassemble.base.util.package_template_filename(yaml_file, package=self.package))
                    elif key == 'template file':
                        if type(value) is not str:
                            raise DAError('The template file must be a string.' + self.idebug(data))
                        self.interview.attachment_options['template_file'] = docassemble.base.util.package_template_filename(value, package=self.package)
                    elif key == 'rtf template file':
                        if type(value) is not str:
                            raise DAError('The rtf template file must be a string.' + self.idebug(data))
                        self.interview.attachment_options['rtf_template_file'] = docassemble.base.util.package_template_filename(value, package=self.package)
        if 'script' in data:
            if type(data) is not str:
                raise DAError("A script section must be plain text." + self.idebug(data))
            self.script = data
        if 'css' in data:
            if type(data) is not str:
                raise DAError("A css section must be plain text." + self.idebug(data))
            self.css = data
        if ('initial' in data and data['initial'] is True) or ('default role' in data):
            #logmessage("Setting a code block to initial\n")
            self.is_initial = True
        else:
            self.is_initial = False
        if 'command' in data and data['command'] in ['exit', 'continue', 'restart', 'leave', 'refresh', 'signin']:
            self.question_type = data['command']
            self.content = TextObject(data.get('url', ''))
            return
        if 'objects' in data:
            if type(data['objects']) is not list:
                data['objects'] = [data['objects']]
                #raise DAError("An objects section must be organized as a list." + self.idebug(data))
            self.question_type = 'objects'
            self.objects = data['objects']
            for item in data['objects']:
                if type(item) is dict:
                    for key in item:
                        self.fields.append(Field({'saveas': key, 'type': 'object', 'objecttype': item[key]}))
                        self.fields_used.add(key)
                elif type(item) is str:
                    self.fields.append(Field({'saveas': item, 'type': 'object', 'objecttype': data['objects'][key]}))
                    self.fields_used.add(item)
                else:
                    raise DAError("An objects section cannot contain a nested list." + self.idebug(data))
        if 'id' in data:
            self.id = data['id']
        for key in ['image sets', 'images']:
            if key not in data:
                continue
            should_append = False
            if type(data[key]) is not dict:
                raise DAError("The '" + key + "' section needs to be a dictionary, not a list or text." + self.idebug(data))
            if key == 'images':
                data[key] = {'unspecified': {'images': data[key]}}
            elif 'images' in data[key] and 'attribution' in data[key]:
                data[key] = {'unspecified': data[key]}
            for setname, image_set in data[key].iteritems():
                if type(image_set) is not dict:
                    if key == 'image sets':
                        raise DAError("Each item in the 'image sets' section needs to be a dictionary, not a list.  Each dictionary item should have an 'images' definition (which can be a dictionary or list) and an optional 'attribution' definition (which must be text)." + self.idebug(data))
                    else:
                        raise DAError("Each item in the 'images' section needs to be a dictionary, not a list." + self.idebug(data))
                if 'attribution' in image_set:
                    if type(image_set['attribution']) in [dict, list]:
                        raise DAError("An attribution in an 'image set' section cannot be a dictionary or a list." + self.idebug(data))
                    attribution = image_set['attribution']
                else:
                    attribution = None
                if 'images' in image_set:
                    if type(image_set['images']) is list:
                        image_list = image_set['images']
                    elif type(image_set['images']) is dict:
                        image_list = [image_set['images']]
                    else:
                        if key == 'image set':
                            raise DAError("An 'images' definition in an 'image set' item must be a dictionary or a list." + self.idebug(data))
                        else:
                            raise DAError("An 'images' section must be a dictionary or a list." + self.idebug(data))                            
                    for image in image_list:
                        if type(image) is not dict:
                            the_image = {str(image): str(image)}
                        else:
                            the_image = image
                        for key, value in the_image.iteritems():
                            self.interview.images[key] = PackageImage(filename=value, attribution=attribution, setname=setname, package=self.package)
        if 'def' in data:
            should_append = False
            if type(data['def']) is not str:
                raise DAError("A def name must be a string." + self.idebug(data))
            if data['def'] not in self.interview.defs:
                self.interview.defs[data['def']] = list()
            if 'mako' in data:
                if type(data['mako']) is str:
                    list_of_defs = [data['mako']]
                elif type(data['mako']) is list:
                    list_of_defs = data['mako']
                else:
                    raise DAError("A mako template definition must be a string or a list of strings." + self.idebug(data))
                for definition in list_of_defs:
                    if type(definition) is not str:
                        raise DAError("A mako template definition must be a string." + self.idebug(data))
                    self.interview.defs[data['def']].append(definition)
        if 'interview help' in data:
            should_append = False
            if type(data['interview help']) is list:
                raise DAError("An interview help section must not be in the form of a list." + self.idebug(data))
            elif type(data['interview help']) is not dict:
                data['interview help'] = {'content': unicode(data['interview help'])}
            if 'heading' in data['interview help']:
                if type(data['interview help']['heading']) not in [dict, list]:
                    help_heading = TextObject(definitions + data['interview help']['heading'])
                else:
                    raise DAError("A heading within an interview help section must be text, not a list or a dictionary." + self.idebug(data))
            else:
                help_heading = None
            if 'content' in data['interview help']:
                if type(data['interview help']['content']) not in [dict, list]:
                    help_content = TextObject(definitions + data['interview help']['content'])
                else:
                    raise DAError("Help content must be text, not a list or a dictionary." + self.idebug(data))
            else:
                raise DAError("No content section was found in an interview help section." + self.idebug(data))
            if self.language not in self.interview.helptext:
                self.interview.helptext[self.language] = list()
            self.interview.helptext[self.language].append({'content': help_content, 'heading': help_heading})
        if 'generic object' in data:
            self.is_generic = True
            self.is_generic_list = False
            self.generic_object = data['generic object']
        elif 'generic list object' in data:
            self.is_generic = True
            self.is_generic_list = True
            self.generic_object = data['generic list object']
        else:
            self.is_generic = False
        if 'metadata' in data:
            should_append = False
            if type(data['metadata']) == dict:
                data['metadata']['origin_path'] = self.from_source.path
                self.interview.metadata.append(data['metadata'])
            else:
                raise DAError("A metadata section must be organized as a dictionary." + self.idebug(data))
        if 'modules' in data:
            if type(data['modules']) is list:
                self.question_type = 'modules'
                self.module_list = data['modules']
            else:
                raise DAError("A modules section must be organized as a list." + self.idebug(data))
        if 'imports' in data:
            if type(data['imports']) is list:
                self.question_type = 'imports'
                self.module_list = data['imports']
            else:
                raise DAError("An imports section must be organized as a list." + self.idebug(data))
        if 'terms' in data:
            should_append = False
            if self.language not in self.interview.terms:
                self.interview.terms[self.language] = dict()
            if type(data['terms']) is list:
                for termitem in data['terms']:
                    if type(termitem) is dict:
                        for term in termitem:
                            lower_term = term.lower()
                            self.interview.terms[self.language][lower_term] = {'definition': termitem[term], 're': re.compile(r"(?i)\b(%s)\b" % lower_term, re.IGNORECASE)}
                    else:
                        raise DAError("A terms section organized as a list must be a list of dictionary items." + self.idebug(data))
            elif type(data['terms']) is dict:
                for term in data['terms']:
                    lower_term = term.lower()
                    self.interview.terms[self.language][lower_term] = {'definition': data['terms'][term], 're': re.compile(r"(?i)\b(%s)\b" % lower_term, re.IGNORECASE)}
            else:
                raise DAError("A terms section must be organized as a dictionary or a list." + self.idebug(data))
        if 'default role' in data:
            if 'code' not in data:
                should_append = False
            if type(data['default role']) is str:
                self.interview.default_role = [data['default role']]
            elif type(data['default role']) is list:
                self.interview.default_role = data['default role']
            else:
                raise DAError("A default role must be a list or a string." + self.idebug(data))
        if 'role' in data:
            if type(data['role']) is str:
                if data['role'] not in self.role:
                    self.role.append(data['role'])
            elif type(data['role']) is list:
                for rolename in data['role']:
                    if data['role'] not in self.role:
                        self.role.append(rolename)
            else:
                raise DAError("The role of a question must be a string or a list." + self.idebug(data))
        else:
            self.role = list()
        if 'include' in data:
            should_append = False
            if type(data['include']) is list:
                for questionPath in data['include']:
                    self.interview.read_from(interview_source_from_string(questionPath, context_interview=self.interview))
            else:
                raise DAError("An include section must be organized as a list." + self.idebug(data))
        if 'if' in data:
            if type(data['if']) == str:
                self.condition = [data['if']]
            elif type(data['if']) == list:
                self.condition = data['if']
            else:
                raise DAError("An if statement must either be text or a list." + self.idebug(data))
        else:
            self.condition = []
        if 'require' in data:
            if type(data['require']) is list:
                self.question_type = 'require'
                try:
                    self.require_list = list(map((lambda x: compile(x, '', 'eval')), data['require']))
                except:
                    logmessage("Compile error in require:\n" + str(data['require']) + "\n" + str(sys.exc_info()[0]))
                    raise
                if 'orelse' in data:
                    if type(data['orelse']) is dict:
                        self.or_else_question = Question(data['orelse'], self.interview, register_target=register_target, source=self.from_source, package=self.package)
                    else:
                        raise DAError("The orelse part of a require section must be organized as a dictionary." + self.idebug(data))
                else:
                    raise DAError("A require section must have an orelse part." + self.idebug(data))
            else:
                raise DAError("A require section must be organized as a list." + self.idebug(data))
        if 'attachment' in data:
            self.attachments = self.process_attachment_list(data['attachment'])
        elif 'attachments' in data:
            self.attachments = self.process_attachment_list(data['attachments'])
        if 'allow emailing' in data:
            self.allow_emailing = data['allow emailing']
        # if 'role' in data:
        #     if type(data['role']) is list:
        #         for rolename in data['role']:
        #             if rolename not in self.role:
        #                 self.role.append(rolename)
        #     elif type(data['role']) is str and data['role'] not in self.role:
        #         self.role.append(data['role'])
        #     else:
        #         raise DAError("A role section must be text or a list." + self.idebug(data))
        if 'progress' in data:
            self.progress = data['progress']
        if 'question' in data:
            self.content = TextObject(definitions + data['question'])
        if 'subquestion' in data:
            self.subcontent = TextObject(definitions + data['subquestion'])
        if 'help' in data:
            self.helptext = TextObject(definitions + data['help'])
        if 'decoration' in data:
            if type(data['decoration']) is dict:
                decoration_list = [data['decoration']]
            elif type(data['decoration']) is list:
                decoration_list = data['decoration']
            else:
                decoration_list = [{'image': str(data['decoration'])}]
            processed_decoration_list = []
            for item in decoration_list:
                if type(item) is dict:
                    the_item = item
                else:
                    the_item = {'image': str(item)}
                item_to_add = dict()
                for key, value in the_item.iteritems():
                    item_to_add[key] = TextObject(value)
                processed_decoration_list.append(item_to_add)
            self.decorations = processed_decoration_list
        if 'signature' in data:
            self.question_type = 'signature'
            self.fields.append(Field({'saveas': data['signature']}))
            self.fields_used.add(data['signature'])
            if 'under' in data:
                self.undertext = TextObject(definitions + data['under'])
        if 'yesno' in data:
            self.fields.append(Field({'saveas': data['yesno'], 'boolean': 1}))
            self.fields_used.add(data['yesno'])
            self.question_type = 'yesno'
        if 'noyes' in data:
            self.fields.append(Field({'saveas': data['noyes'], 'boolean': -1}))
            self.fields_used.add(data['noyes'])
            self.question_type = 'noyes'
        if 'sets' in data:
            if type(data['sets']) is str:
                self.fields_used.add(data['sets'])
            elif type(data['sets']) is list:
                for key in data['sets']:
                    self.fields_used.add(key)
            else:
                raise DAError("A sets phrase must be text or a list." + self.idebug(data))
        if 'event' in data:
            if type(data['event']) is str:
                self.fields_used.add(data['event'])
            elif type(data['event']) is list:
                for key in data['event']:
                    self.fields_used.add(key)
            else:
                raise DAError("An event phrase must be text or a list." + self.idebug(data))
        if 'choices' in data or 'buttons' in data:
            if 'field' in data:
                uses_field = True
            else:
                uses_field = False
            if 'shuffle' in data and data['shuffle']:
                shuffle = True
            else:
                shuffle = False
            if 'choices' in data:
                has_code, choices = self.parse_fields(data['choices'], register_target, uses_field)
                field_data = {'choices': choices, 'shuffle': shuffle}
                if has_code:
                    field_data['has_code'] = True
                self.question_variety = 'radio'
            elif 'buttons' in data:
                has_code, choices = self.parse_fields(data['buttons'], register_target, uses_field)
                field_data = {'choices': choices, 'shuffle': shuffle}
                if has_code:
                    field_data['has_code'] = True
                self.question_variety = 'buttons'
            if uses_field:
                self.fields_used.add(data['field'])
                field_data['saveas'] = data['field']
                if 'datatype' in data and 'type' not in field_data:
                    field_data['type'] = data['datatype']
            self.fields.append(Field(field_data))
            self.question_type = 'multiple_choice'
        elif 'field' in data:
            self.fields_used.add(data['field'])
            field_data = {'saveas': data['field']}
            self.fields.append(Field(field_data))
            self.question_type = 'settrue'
        if 'need' in data:
            if type(data['need']) == str:
                need_list = [data['need']]
            elif type(data['need']) == list:
                need_list = data['need']
            else:
                raise DAError("A need phrase must be text or a list." + self.idebug(data))
            try:
                self.need = list(map((lambda x: compile(x, '', 'exec')), need_list))
            except:
                logmessage("Compile error in need code:\n" + str(data['need']) + "\n" + str(sys.exc_info()[0]))
                raise
        if 'template' in data and 'content file' in data:
            if type(data['content file']) is not list:
                data['content file'] = [data['content file']]
            data['content'] = ''
            for content_file in data['content file']:
                if type(content_file) is not str:
                    raise DAError('A content file must be specified as text or a list of text filenames' + self.idebug(data))
                file_to_read = docassemble.base.util.package_template_filename(content_file, package=self.package)
                if os.path.isfile(file_to_read) and os.access(file_to_read, os.R_OK):
                    with open(file_to_read, 'r') as the_file:
                        data['content'] += the_file.read()
                else:
                    raise DAError('Unable to read content file ' + str(target['content file']) + ' after trying to find it at ' + str(file_to_read) + self.idebug(data))
        if 'template' in data and 'content' in data:
            if type(data['template']) in (list, dict):
                raise DAError("A template must designate a single variable expressed as text." + self.idebug(data))
            if type(data['content']) in (list, dict):
                raise DAError("The content of a template must be expressed as text." + self.idebug(data))
            self.fields_used.add(data['template'])
            field_data = {'saveas': data['template']}
            self.fields.append(Field(field_data))
            self.content = TextObject(definitions + data['content'])
            if 'subject' in data:
                self.subcontent = TextObject(definitions + data['subject'])
            else:
                self.subcontent = TextObject("")
            self.question_type = 'template'
        if 'code' in data:
            self.question_type = 'code'
            if type(data['code']) == str:
                try:
                    self.compute = compile(data['code'], '', 'exec')
                    self.sourcecode = data['code']
                except:
                    logmessage("Compile error in code:\n" + unicode(data['code']) + "\n" + str(sys.exc_info()[0]))
                    raise
                find_fields_in(data['code'], self.fields_used, self.names_used)
            else:
                raise DAError("A code section must be text, not a list or a dictionary." + self.idebug(data))
        if 'fields' in data:
            self.question_type = 'fields'
            if type(data['fields']) is not list:
                raise DAError("The fields must be written in the form of a list." + self.idebug(data))
            else:
                field_number = 0
                for field in data['fields']:
                    if type(field) is dict:
                        field_info = {'type': 'text', 'number': field_number}
                        for key in field:
                            if key == 'required':
                                field_info['required'] = field[key]
                            elif key == 'default' or key == 'hint' or key == 'help':
                                if type(field[key]) is not dict and type(field[key]) is not list:
                                    field_info[key] = TextObject(definitions + unicode(field[key]))
                            elif key == 'datatype':
                                field_info['type'] = field[key]
                                if field[key] in ['yesno', 'yesnowide'] and 'required' not in field_info:
                                    field_info['required'] = False
                            elif key == 'code':
                                field_info['choicetype'] = 'compute'
                                field_info['selections'] = {'compute': compile(field[key], '', 'eval'), 'sourcecode': field[key]}
                            elif key == 'choices':
                                field_info['choicetype'] = 'manual'
                                field_info['selections'] = process_selections(field[key])
                            elif key == 'note':
                                field_info['type'] = 'note'
                                if 'extras' not in field_info:
                                    field_info['extras'] = dict()
                                field_info['extras']['note'] = TextObject(definitions + unicode(field[key]))
                            elif key == 'html':
                                if 'extras' not in field_info:
                                    field_info['extras'] = dict()
                                field_info['type'] = 'html'
                                field_info['extras'][key] = TextObject(definitions + unicode(field[key]))
                            elif key in ['css', 'script']:
                                if 'extras' not in field_info:
                                    field_info['extras'] = dict()
                                if field_info['type'] == 'text':
                                    field_info['type'] = key
                                field_info['extras'][key] = TextObject(definitions + unicode(field[key]))
                            elif key == 'shuffle':
                                field_info['shuffle'] = field[key]
                            else:
                                field_info['label'] = key
                                field_info['saveas'] = field[key]
                        if 'saveas' in field_info:
                            self.fields.append(Field(field_info))
                            self.fields_used.add(field_info['saveas'])
                        elif 'type' in field_info and field_info['type'] in ['note', 'html', 'script', 'css']:
                            self.fields.append(Field(field_info))
                        else:
                            raise DAError("A field was listed without indicating a label or a variable name, and the field was not a note or raw HTML." + self.idebug(field_info))
                    else:
                        raise DAError("Each individual field in a list of fields must be expressed as a dictionary item, e.g., ' - Fruit: user.favorite_fruit'." + self.idebug(data))
                    field_number += 1
        if not hasattr(self, 'question_type'):
            if hasattr(self, 'content'):
                self.question_type = 'deadend'
        if should_append:
            if not hasattr(self, 'question_type'):
                raise DAError("No question type could be determined for this section." + self.idebug(data))
            if main_list:
                self.interview.questions_list.append(self)
            self.number = self.interview.next_number()
            #self.number = len(self.interview.questions_list) - 1
            self.name = "Question_" + str(self.number)
        if hasattr(self, 'id'):
            try:
                self.interview.questions_by_id[self.id].append(self)
            except:
                self.interview.questions_by_id[self.id] = [self]
        for field_name in self.fields_used:
            if field_name not in self.interview.questions:
                self.interview.questions[field_name] = dict()
            if self.language not in self.interview.questions[field_name]:
                self.interview.questions[field_name][self.language] = list()
            self.interview.questions[field_name][self.language].append(register_target)
            if self.is_generic:
                if self.generic_object not in self.interview.generic_questions:
                    self.interview.generic_questions[self.generic_object] = dict()
                if field_name not in self.interview.generic_questions[self.generic_object]:
                    self.interview.generic_questions[self.generic_object][field_name] = dict()
                if self.language not in self.interview.generic_questions[self.generic_object][field_name]:
                    self.interview.generic_questions[self.generic_object][field_name][self.language] = list()
                self.interview.generic_questions[self.generic_object][field_name][self.language].append(register_target)

    def process_attachment_list(self, target):
        if type(target) is list:
            return(list(map((lambda x: self.process_attachment(x)), target)))
        else:
            return([self.process_attachment(target)])

    def process_attachment(self, target):
        metadata = dict()
        variable_name = str()
        defs = list()
        options = dict()
        if type(target) is dict:
            if 'filename' not in target:
                target['filename'] = word("Document")
            if 'name' not in target:
                target['name'] = word("Document")
            if 'description' not in target:
                target['description'] = ''
            if 'initial yaml' in target:
                if type(target['initial yaml']) is not list:
                    target['initial yaml'] = [target['initial yaml']]
                options['initial_yaml'] = list()
                for yaml_file in target['initial yaml']:
                    if type(yaml_file) is not str:
                        raise DAError('An initial yaml file must be a string.' + self.idebug(target))
                    options['initial_yaml'].append(docassemble.base.util.package_template_filename(yaml_file, package=self.package))
            if 'additional yaml' in target:
                if type(target['additional yaml']) is not list:
                    target['additional yaml'] = [target['additional yaml']]
                options['additional_yaml'] = list()
                for yaml_file in target['additional yaml']:
                    if type(yaml_file) is not str:
                        raise DAError('An additional yaml file must be a string.' + self.idebug(target))
                    options['additional_yaml'].append(docassemble.base.util.package_template_filename(yaml_file, package=self.package))
            if 'template file' in target:
                if type(target['template file']) is not str:
                    raise DAError('The template file must be a string.' + self.idebug(target))
                options['template_file'] = docassemble.base.util.package_template_filename(target['template file'], package=self.package)
            if 'rtf template file' in target:
                if type(target['rtf template file']) is not str:
                    raise DAError('The rtf template file must be a string.' + self.idebug(target))
                options['rtf_template_file'] = docassemble.base.util.package_template_filename(target['rtf template file'], package=self.package)
            if 'usedefs' in target:
                if type(target['usedefs']) is str:
                    the_list = [target['usedefs']]
                elif type(target['usedefs']) is list:
                    the_list = target['usedefs']
                else:
                    raise DAError('The usedefs included in an attachment must be specified as a list of strings or a single string.' + self.idebug(target))
                for def_key in the_list:
                    if type(def_key) is not str:
                        raise DAError('The defs in an attachment must be strings.' + self.idebug(target))
                    if def_key not in self.interview.defs:
                        raise DAError('Referred to a non-existent def "' + def_key + '."  All defs must be defined before they are used.' + self.idebug(target))
                    defs.extend(self.interview.defs[def_key])
            if 'valid_formats' in target:
                if type(target['valid_formats']) is str:
                    target['valid_formats'] = [target['valid_formats']]
                elif type(target['valid_formats']) is not list:
                    raise DAError('Unknown data type in attachment valid_formats.' + self.idebug(target))
            else:
                target['valid_formats'] = ['*']
            if 'variable_name' in target:
                variable_name = target['variable_name']
                self.fields_used.add(target['variable_name'])
            if 'metadata' in target:
                if type(target['metadata']) is not dict:
                    raise DAError('Unknown data type ' + str(type(target['metadata'])) + ' in attachment metadata.' + self.idebug(target))
                for key in target['metadata']:
                    data = target['metadata'][key]
                    if data is list:
                        for sub_data in data:
                            if sub_data is not str:
                                raise DAError('Unknown data type ' + str(type(sub_data)) + ' in list in attachment metadata' + self.idebug(target))
                        newdata = list(map((lambda x: TextObject(x)), data))
                        metadata[key] = newdata
                    elif type(data) is str:
                        metadata[key] = TextObject(data)
                    elif type(data) is bool:
                        metadata[key] = data
                    else:
                        raise DAError('Unknown data type ' + str(type(data)) + ' in key in attachment metadata' + self.idebug(target))
            if 'content file' in target:
                if type(target['content file']) is not list:
                    target['content file'] = [target['content file']]
                target['content'] = ''
                for content_file in target['content file']:
                    if type(content_file) is not str:
                        raise DAError('A content file must be specified as text or a list of text filenames' + self.idebug(target))
                    file_to_read = docassemble.base.util.package_template_filename(content_file, package=self.package)
                    if os.path.isfile(file_to_read) and os.access(file_to_read, os.R_OK):
                        with open(file_to_read, 'r') as the_file:
                            target['content'] += the_file.read()
                    else:
                        raise DAError('Unable to read content file ' + str(target['content file']) + ' after trying to find it at ' + str(file_to_read) + self.idebug(target))
            if 'content' not in target:
                raise DAError("No content provided in attachment")
            return({'name': TextObject(target['name']), 'filename': TextObject(target['filename']), 'description': TextObject(target['description']), 'content': TextObject("\n".join(defs) + "\n" + target['content']), 'valid_formats': target['valid_formats'], 'metadata': metadata, 'variable_name': variable_name, 'options': options})
        elif type(target) is str:
            return({'name': TextObject('Document'), 'filename': TextObject('document'), 'content': TextObject(target), 'valid_formats': ['*'], 'metadata': metadata, 'variable_name': variable_name, 'options': options})
        else:
            raise DAError("Unknown data type in process_attachment")

    def ask(self, user_dict, the_x, the_i):
        logmessage("asking: " + str(self.subcontent.original_text))
        if the_x != 'None':
            exec("x = " + the_x, user_dict)
        if the_i != 'None':
            exec("i = " + the_i, user_dict)
        question_text = self.content.text(user_dict)
        if self.subcontent is not None:
            subquestion = self.subcontent.text(user_dict)
        else:
            subquestion = None
        if self.undertext is not None:
            undertext = self.undertext.text(user_dict)
        else:
            undertext = None
        if self.helptext is not None:
            help_text_list = [{'heading': None, 'content': self.helptext.text(user_dict)}]
        else:
            help_text_list = list()
        interview_help_text_list = self.interview.processed_helptext(user_dict, self.language)
        if len(interview_help_text_list) > 0:
            help_text_list.extend(interview_help_text_list)
        if self.decorations is not None:
            decorations = list()
            for decoration_item in self.decorations:
                processed_item = dict()
                for key, value in decoration_item.iteritems():
                    processed_item[key] = value.text(user_dict)
                decorations.append(processed_item)
        else:
            decorations = None
        selectcompute = dict()
        defaults = dict()
        hints = dict()
        helptexts = dict()
        extras = dict()
        for field in self.fields:
            if hasattr(field, 'has_code') and field.has_code:
                selections = list()
                for choice in field.choices:
                    for key in choice:
                        value = choice[key]
                        if key == 'compute' and type(value) is CodeType:
                            selections.extend(process_selections(eval(value, user_dict)))
                        else:
                            selections.append([value, key])
                selectcompute[field.number] = selections
            if hasattr(field, 'choicetype') and field.choicetype == 'compute':
                selectcompute[field.number] = process_selections(eval(field.selections['compute'], user_dict))
            if hasattr(field, 'extras'):
                for key in ['note', 'html', 'script', 'css']:
                    if key in field.extras:
                        if key not in extras:
                            extras[key] = dict()
                        extras[key][field.number] = field.extras[key].text(user_dict)
            if hasattr(field, 'saveas'):
                try:
                    defaults[field.number] = eval(field.saveas, user_dict)
                except:
                    if hasattr(field, 'default'):
                        defaults[field.number] = field.default.text(user_dict)
                if hasattr(field, 'helptext'):
                    helptexts[field.number] = field.helptext.text(user_dict)
                if hasattr(field, 'hint'):
                    hints[field.number] = field.hint.text(user_dict)
        attachment_text = self.processed_attachments(user_dict, the_x=the_x, the_i=the_i)
        if 'role' in user_dict:
            current_role = user_dict['role']
            if len(self.role) > 0:
                if current_role not in self.role and 'role_event' not in self.fields_used and self.question_type not in ['exit', 'continue', 'restart', 'leave', 'refresh', 'signin']:
                    #logmessage("Calling role_event with " + ", ".join(self.fields_used))
                    user_dict['role_needed'] = self.role
                    raise NameError("name 'role_event' is not defined")
            elif self.interview.default_role is not None and current_role not in self.interview.default_role and 'role_event' not in self.fields_used and self.question_type not in ['exit', 'continue', 'restart', 'leave', 'refresh', 'signin']:
                #logmessage("Calling role_event with " + ", ".join(self.fields_used))
                user_dict['role_needed'] = self.interview.default_role
                raise NameError("name 'role_event' is not defined")
        return({'type': 'question', 'question_text': question_text, 'subquestion_text': subquestion, 'under_text': undertext, 'decorations': decorations, 'help_text': help_text_list, 'attachments': attachment_text, 'question': self, 'variable_x': the_x, 'variable_i': the_i, 'selectcompute': selectcompute, 'defaults': defaults, 'hints': hints, 'helptexts': helptexts, 'extras': extras})
    def processed_attachments(self, user_dict, **kwargs):
        return(list(map((lambda x: self.make_attachment(x, user_dict, **kwargs)), self.attachments)))
    def parse_fields(self, the_list, register_target, uses_field):
        result_list = list()
        has_code = False
        if type(the_list) is not list:
            raise DAError("Multiple choices need to be provided in list form, not dictionary form.  " + self.idebug(the_list))
        for the_dict in the_list:
            if type(the_dict) is str:
                the_dict = {the_dict: the_dict}
            elif type(the_dict) is not dict:
                raise DAError("Unknown data type for the_dict in parse_fields.  " + self.idebug(the_list))
            result_dict = dict()
            for key, value in the_dict.iteritems():
                if key == 'image':
                    result_dict['image'] = value
                    continue
                if uses_field:
                    if key == 'code':
                        has_code = True
                        result_dict['compute'] = compile(value, '', 'eval')
                    else:
                        result_dict[key] = value
                elif type(value) == dict:
                    result_dict[key] = Question(value, self.interview, register_target=register_target, source=self.from_source, package=self.package)
                elif type(value) == str:
                    if value in ['exit', 'leave'] and 'url' in the_dict:
                        result_dict[key] = Question({'command': value, 'url': the_dict['url']}, self.interview, register_target=register_target, source=self.from_source, package=self.package)
                    elif value in ["continue", "restart", "refresh", "signin", "exit", "leave"]:
                        result_dict[key] = Question({'command': value}, self.interview, register_target=register_target, source=self.from_source, package=self.package)
                    elif key == 'url' and 'link' in the_dict.values():
                        pass
                    else:
                        result_dict[key] = value
                elif type(value) == bool:
                    result_dict[key] = value
                else:
                    raise DAError("Unknown data type in parse_fields:" + str(type(value)) + ".  " + self.idebug(the_list))
            result_list.append(result_dict)
        return(has_code, result_list)
    def mark_as_answered(self, user_dict):
        user_dict['_internal']['answered'].add(self.name)
        logmessage("1 Question name was " + self.name)
        return
    def follow_multiple_choice(self, user_dict):
        logmessage("follow_multiple_choice")
        if self.name:
            logmessage("question is " + self.name)
        else:
            logmessage("question has no name")
        if self.name and self.name in user_dict['_internal']['answers']:
            self.mark_as_answered(user_dict)
            #logmessage("question in answers")
            #user_dict['_internal']['answered'].add(self.name)
            #logmessage("2 Question name was " + self.name)
            the_choice = self.fields[0].choices[user_dict['_internal']['answers'][self.name]]
            for key in the_choice:
                if key == 'image':
                    continue
                logmessage("Setting target")
                target = the_choice[key]
                break
            if target:
                logmessage("Target defined")
                if type(target) is str:
                    pass
                elif isinstance(target, Question):
                    logmessage("Reassigning question")
                    # self.mark_as_answered(user_dict)
                    return(target.follow_multiple_choice(user_dict))
        return(self)
    def make_attachment(self, attachment, user_dict, **kwargs):
        result = {'name': attachment['name'].text(user_dict), 'filename': attachment['filename'].text(user_dict), 'description': attachment['description'].text(user_dict), 'valid_formats': attachment['valid_formats']}
        result['markdown'] = dict();
        result['content'] = dict();
        result['file'] = dict();
        if '*' in attachment['valid_formats']:
            formats_to_use = ['html', 'rtf', 'pdf', 'tex']
        else:
            formats_to_use = attachment['valid_formats']
        for doc_format in formats_to_use:
            if doc_format in ['pdf', 'rtf', 'tex']:
                the_markdown = ""
                metadata = dict()
                if len(attachment['metadata']) > 0:
                    for key in attachment['metadata']:
                        data = attachment['metadata'][key]
                        if type(data) is bool:
                            metadata[key] = data
                        elif type(data) is list:
                            metadata[key] = textify(data)
                        else:
                            metadata[key] = data.text(user_dict)
                    the_markdown += "---\n" + yaml.safe_dump(metadata, default_flow_style=False, default_style = '|') + "...\n"
                the_markdown += attachment['content'].text(user_dict)
                if emoji_match.search(the_markdown) and len(self.interview.images) > 0:
                    the_markdown = emoji_match.sub((lambda x: docassemble.base.filter.emoji_insert(x.group(1), images=self.interview.images)), the_markdown)
                result['markdown'][doc_format] = the_markdown
                converter = Pandoc()
                converter.output_format = doc_format
                converter.input_content = the_markdown
                #logmessage("Markdown is:\n" + the_markdown + "END");
                if 'initial_yaml' in attachment['options']:
                    converter.initial_yaml = attachment['options']['initial_yaml']
                elif 'initial_yaml' in self.interview.attachment_options:
                    converter.initial_yaml = self.interview.attachment_options['initial_yaml']
                if 'additional_yaml' in attachment['options']:
                    converter.additional_yaml = attachment['options']['additional_yaml']
                elif 'additional_yaml' in self.interview.attachment_options:
                    converter.additional_yaml = self.interview.attachment_options['additional_yaml']
                if doc_format == 'rtf':
                    if 'rtf_template_file' in attachment['options']:
                        converter.template_file = attachment['options']['rtf_template_file']
                    elif 'rtf_template_file' in self.interview.attachment_options:
                        converter.template_file = self.interview.attachment_options['rtf_template_file']
                else:
                    if 'template_file' in attachment['options']:
                        converter.template_file = attachment['options']['template_file']
                    elif 'template_file' in self.interview.attachment_options:
                        converter.template_file = self.interview.attachment_options['template_file']
                converter.metadata = metadata
                converter.convert()
                result['file'][doc_format] = converter.output_filename
                result['content'][doc_format] = result['markdown'][doc_format]
            elif doc_format in ['html']:
                result['markdown'][doc_format] = attachment['content'].text(user_dict)
                if emoji_match.search(result['markdown'][doc_format]) and len(self.interview.images) > 0:
                    result['markdown'][doc_format] = emoji_match.sub((lambda x: docassemble.base.filter.emoji_html(x.group(1), images=self.interview.images)), result['markdown'][doc_format])
                result['content'][doc_format] = docassemble.base.filter.markdown_to_html(result['markdown'][doc_format], use_pandoc=True)
                #logmessage("output was:\n" + repr(result['content'][doc_format]))
        if attachment['variable_name']:
            string = attachment['variable_name'] + " = DAFileCollection('" + attachment['variable_name'] + "')"
            #sys.stderr.write("Executing " + string + "\n")
            exec(string, user_dict)
            for doc_format in result['file']:
                variable_string = attachment['variable_name'] + '.' + doc_format
                filename = result['filename'] + '.' + doc_format
                file_number, extension, mimetype = save_numbered_file(filename, result['file'][doc_format])
                if file_number is None:
                    raise Exception("Could not save numbered file")
                string = variable_string + " = DAFile('" + variable_string + "', filename='" + str(filename) + "', number=" + str(file_number) + ", mimetype='" + str(mimetype) + "', extension='" + str(extension) + "')"
                #sys.stderr.write("Executing " + string + "\n")
                exec(string, user_dict)
        return(result)


def interview_source_from_string(path, **kwargs):
    if path is None:
        raise DAError("Passed None to interview_source_from_string")
    if re.search(r'^https*://', path):
        new_source = InterviewSourceURL(path=path)
        if new_source.update():
            return new_source
    context_interview = kwargs.get('context_interview', None)
    if context_interview is not None:
        new_source = context_interview.source.append(path)
        if new_source is not None:
            return new_source
    #sys.stderr.write("Trying to find it\n")
    for the_filename in [docassemble.base.util.package_question_filename(path), docassemble.base.util.standard_question_filename(path), docassemble.base.util.absolute_filename(path)]:
        #sys.stderr.write("Trying " + str(the_filename) + " with path " + str(path) + "\n")
        if the_filename is not None:
            new_source = InterviewSourceFile(filepath=the_filename, path=path)
            if new_source.update():
                return(new_source)
    raise DAError("YAML file " + str(path) + " not found")

class Interview:
    def __init__(self, **kwargs):
        #questionFilepath = kwargs.get('questionFilepath', None)
        #self.questionPath = None
        #self.rootDirectory = None
        self.source = None
        self.questions = dict()
        self.generic_questions = dict()
        self.questions_by_id = dict()
        self.questions_list = list()
        self.images = dict()
        self.metadata = list()
        self.helptext = dict()
        self.defs = dict()
        self.terms = dict()
        self.question_index = 0
        self.default_role = None
        self.attachment_options = dict()
        if 'source' in kwargs:
            self.read_from(kwargs['source'])
    def next_number(self):
        self.question_index += 1
        return(self.question_index - 1)
    def read_from(self, source):
        if self.source is None:
            self.source = source
            #self.firstPath = source.path
            #self.rootDirectory = source.directory
        if hasattr(source, 'package'):
            source_package = source.package
        else:
            source_package = None
        #for document in yaml.load_all(source.content):
        for source_code in document_match.split(source.content):
            source_code = remove_trailing_dots.sub('', source_code)
            document = yaml.load(source_code)
            if document is not None:
                question = Question(document, self, source=source, package=source_package, source_code=source_code)
    def processed_helptext(self, user_dict, language):
        result = list()
        if language in self.helptext:
            for source in self.helptext[language]:
                help_item = dict()
                if source['heading'] is None:
                    help_item['heading'] = None
                else:
                    help_item['heading'] = source['heading'].text(user_dict)
                help_item['content'] = source['content'].text(user_dict)
                result.append(help_item)
        return result
    def assemble(self, user_dict, *args):
        #logmessage("I am assembling.")
        user_dict['_internal']['tracker'] += 1
        if len(args):
            interview_status = args[0]
        else:
            interview_status = InterviewStatus()
        interview_status.set_tracker(user_dict['_internal']['tracker'])
        # if '_answered' not in user_dict:
        #     user_dict['_answered'] = set()
        # if '_answers' not in user_dict:
        #     user_dict['_answers'] = dict()
        docassemble.base.util.reset_language_locale()
        interview_status.current_info.update({'default_role': self.default_role})
        user_dict['current_info'] = interview_status.current_info
        for question in self.questions_list:
            if question.question_type == 'imports':
                #logmessage("Found imports")
                for module_name in question.module_list:
                    #logmessage("Imported a module " + module_name)
                    exec('import ' + module_name, user_dict)
            if question.question_type == 'modules':
                for module_name in question.module_list:
                    #logmessage("Imported from module " + module_name)
                    exec('from ' + module_name + ' import *', user_dict)
        while True:
            try:
                for question in self.questions_list:
                    if question.question_type == 'code' and question.is_initial:
                        #logmessage("Running some code:\n\n" + question.sourcecode)
                        if debug:
                            interview_status.seeking.append({'question': question, 'reason': 'initial'})
                        exec(question.compute, user_dict)
                    if question.name and question.name in user_dict['_internal']['answered']:
                        #logmessage("Skipping " + question.name + " because answered")
                        continue
                    if question.question_type == 'code' and question.is_mandatory:
                        if debug:
                            interview_status.seeking.append({'question': question, 'reason': 'mandatory code'})
                        #logmessage("Running some code:\n\n" + question.sourcecode)
                        #logmessage("Question name is " + question.name)
                        exec(question.compute, user_dict)
                        #logmessage("Code completed")
                        if question.name:
                            user_dict['_internal']['answered'].add(question.name)
                    if hasattr(question, 'content') and question.name and question.is_mandatory:
                        if debug:
                            interview_status.seeking.append({'question': question, 'reason': 'mandatory question'})
                        if question.name and question.name in user_dict['_internal']['answers']:
                            #logmessage("in answers")
                            #question.mark_as_answered(user_dict)
                            interview_status.populate(question.follow_multiple_choice(user_dict).ask(user_dict, 'None', 'None'))
                        else:
                            interview_status.populate(question.ask(user_dict, 'None', 'None'))
                        raise MandatoryQuestion()
            except NameError as errMess:
                missingVariable = extract_missing_name(errMess)
                #missingVariable = str(errMess).split("'")[1]
                #logmessage(str(errMess))
                question_result = self.askfor(missingVariable, user_dict, seeking=interview_status.seeking)
                if question_result['type'] == 'continue':
                    #logmessage("Continuing after asking for " + missingVariable + "...")
                    continue
                else:
                    #pp = pprint.PrettyPrinter(indent=4)
                    #logmessage("Need to ask:\n  " + question_result['question_text'] + "\n" + "type is " + str(question_result['question'].question_type) + "\n" + pp.pformat(question_result) + "\n" + pp.pformat(question_result['question']))
                    #logmessage("Need to ask:\n  " + question_result['question_text'])
                    interview_status.populate(question_result)
                    break
            except AttributeError as errMess:
                #logmessage(str(errMess.args))
                raise DAError('Got error ' + str(errMess))
            except MandatoryQuestion:
                break
            else:
                raise DAError('Docassemble has finished executing all code blocks marked as initial or mandatory, and finished asking all questions marked as mandatory (if any).  It is a best practice to end your interview with a question that says goodbye and offers an Exit button.')
        return(pickleable_objects(user_dict))
    def askfor(self, missingVariable, user_dict, **kwargs):
        variable_stack = kwargs.get('variable_stack', set())
        language = get_language()
        seeking = kwargs.get('seeking', list())
        if debug:
            seeking.append({'variable': missingVariable})
        logmessage("I don't have " + missingVariable)
        if missingVariable in variable_stack:
            raise DAError("Infinite loop: " + missingVariable + " already looked for")
        variable_stack.add(missingVariable)
        found_generic = False
        realMissingVariable = missingVariable
        totry = [{'real': missingVariable, 'vari': missingVariable}]
        #logmessage("moo1")
        m = match_inside_brackets.search(missingVariable)
        if m:
            newMissingVariable = re.sub('\[[^\]+]\]', '[i]', missingVariable)
            totry.insert(0, {'real': missingVariable, 'vari': newMissingVariable})
        #logmessage("Length of totry is " + str(len(totry)))
        for mv in totry:
            realMissingVariable = mv['real']
            missingVariable = mv['vari']
            #logmessage("Trying missingVariable " + missingVariable)
            questions_to_try = list()
            generic_needed = True;
            if missingVariable in self.questions:
                for lang in [language, '*']:
                    if lang in self.questions[missingVariable]:
                        for the_question in reversed(self.questions[missingVariable][lang]):
                            questions_to_try.append((the_question, False, 'None', 'None', missingVariable))
                        generic_needed = False
            #components = missingVariable.split(".")
            #realComponents = realMissingVariable.split(".")
            components = dot_split.split(missingVariable)[1::2]
            realComponents = dot_split.split(realMissingVariable)[1::2]
            logmessage("Vari Components are " + str(components))
            logmessage("Real Components are " + str(realComponents))
            n = len(components)
            # if n == 1:
            #     if generic_needed:
            #         logmessage("There is no question for " + missingVariable)
            #     else:
            #         logmessage("There are no generic options for " + missingVariable)
            if n != 1:
                found_x = 0;
                for i in range(1, n):
                    if found_x:
                        break;
                    sub_totry = [{'var': "x." + ".".join(components[i:n]), 'realvar': "x." + ".".join(realComponents[i:n]), 'root': ".".join(realComponents[0:i]), 'root_for_object': ".".join(realComponents[0:i])}]
                    m = match_brackets_at_end.search(sub_totry[0]['root'])
                    if m:
                        before_brackets = m.group(1)
                        brackets_part = m.group(2)
                        sub_totry.insert(0, {'var': "x[i]." + ".".join(components[i:n]), 'realvar': "x" + brackets_part + "." + ".".join(realComponents[i:n]), 'root': before_brackets, 'root_for_object': before_brackets + brackets_part})
                    for d in sub_totry:
                        the_i_to_use = 'None'
                        if found_x:
                            break;
                        var = d['var']
                        realVar = d['realvar']
                        mm = match_inside_brackets.findall(realVar)
                        if (mm):
                            if len(mm) > 1:
                                #logmessage("Variable " + var + " is no good because it has more than one iterator")
                                continue;
                            the_i_to_use = mm[0];
                        root = d['root']
                        root_for_object = d['root_for_object']
                        #logmessage("testing variable " + var + " and root " + root + " and root for object " + root_for_object)
                        try:
                            root_evaluated = eval(root_for_object, user_dict)
                            generic_object = type(root_evaluated).__name__
                            if generic_object in self.generic_questions and var in self.questions and var in self.generic_questions[generic_object] and (language in self.generic_questions[generic_object][var] or '*' in self.generic_questions[generic_object][var]) and (language in self.questions[var] or '*' in self.questions[var]):
                                #logmessage("foo1" + var)
                                for lang in [language, '*']:
                                    #logmessage("foo2" + lang)
                                    if lang in self.questions[var]:
                                        #logmessage("foo3" + var + lang)
                                        for the_question_to_use in reversed(self.questions[var][lang]):
                                            #logmessage("foo4 " + var + lang)
                                            questions_to_try.append((the_question_to_use, True, root, the_i_to_use, var))
                                missingVariable = var
                                found_generic = True
                                found_x = 1
                                break
                            #logmessage("I should be looping around now")
                        except:
                            logmessage("variable did not exist in user_dict: " + str(sys.exc_info()[0]))
                if generic_needed and not found_generic: # or is_iterator
                    #logmessage("There is no question for " + missingVariable)
                    continue
            while True:
                try:
                    for the_question, is_generic, the_x, the_i, missing_var in questions_to_try:
                        logmessage("missing_var is " + str(missing_var))
                        logmessage("Trying question of type " + str(the_question.question_type))
                        question = the_question.follow_multiple_choice(user_dict)
                        logmessage("Back from follow_multiple_choice")
                        logmessage("Trying a question of type " + str(question.question_type))
                        if is_generic:
                            #logmessage("Yes it's generic")
                            if question.is_generic:
                                #logmessage("Yes question is generic")
                                if question.generic_object != generic_object:
                                    #logmessage("Object mismatch")
                                    continue
                                #logmessage("ok")
                            else:
                                #logmessage("No question is not generic")
                                continue
                        if debug:
                            seeking.append({'question': question, 'reason': 'asking'})
                        if question.question_type == "objects":
                            for keyvalue in question.objects:
                                for variable in keyvalue:
                                    object_type = keyvalue[variable]
                                    if re.search(r"\.", variable):
                                        m = re.search(r"(.*)\.(.*)", variable)
                                        variable = m.group(1)
                                        attribute = m.group(2)
                                        command = variable + ".initializeAttribute(name='" + attribute + "', objectType=" + object_type + ")"
                                        #logmessage("Running " + command)
                                        exec(command, user_dict)
                                    else:
                                        command = variable + ' = ' + object_type + '("' + variable + '")'
                                        exec(command, user_dict)
                            #question.mark_as_answered(user_dict)
                            return({'type': 'continue'})
                        if question.question_type == "template":
                            exec(question.fields[0].saveas + ' = DATemplate(' + "'" + question.fields[0].saveas + "', content=" + '"""' + question.content.text(user_dict).rstrip().encode('unicode_escape') + '""", subject="""' + question.subcontent.text(user_dict).rstrip().encode('unicode_escape') + '""")', user_dict)
                            #question.mark_as_answered(user_dict)
                            return({'type': 'continue'})
                        if question.question_type == "code":
                            #logmessage("Running some code:\n\n" + question.sourcecode)
                            if is_generic:
                                if the_x != 'None':
                                    exec("x = " + the_x, user_dict)
                                    #logmessage("Set x")
                                if the_i != 'None':
                                    exec("i = " + the_i, user_dict)
                                    #logmessage("Set i")
                            exec(question.compute, user_dict)
                            #logmessage("the missing variable is " + str(missing_var))
                            if missing_var in variable_stack:
                                variable_stack.remove(missing_var)
                            try:
                                eval(missing_var, user_dict)
                                question.mark_as_answered(user_dict)
                                return({'type': 'continue'})
                            except:
                                #logmessage("Try another method of setting the variable")
                                continue
                        else:
                            logmessage("Question type is " + question.question_type)
                            #logmessage("Ask:\n" + question.content.original_text)
                            if question.question_type == 'continue':
                                continue
                            return question.ask(user_dict, the_x, the_i)
                    raise DAError("Found a reference to a variable '" + missingVariable + "' that could not be looked up in the question file or in any of the files incorporated by reference into the question file.")
                except NameError as errMess:
                    newMissingVariable = extract_missing_name(errMess)
                    #newMissingVariable = str(errMess).split("'")[1]
                    #logmessage(str(errMess))
                    question_result = self.askfor(newMissingVariable, user_dict, variable_stack=variable_stack, seeking=seeking)
                    if question_result['type'] == 'continue':
                        continue
                    return(question_result)
        raise DAError("Found a reference to a variable '" + missingVariable + "' that could not be looked up in the question file or in any of the files incorporated by reference into the question file.  The askfor function reached its end.")
        
class myextract(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
    def visit_Name(self, node):
        self.stack.append(node.id)
        ast.NodeVisitor.generic_visit(self, node)
    def visit_Attribute(self, node):
        self.stack.append(node.attr)
        ast.NodeVisitor.generic_visit(self, node)

class myvisitnode(ast.NodeVisitor):
    def __init__(self):
        self.names = {}
        self.targets = {}
        self.depth = 0;
    def generic_visit(self, node):
        #logmessage(' ' * self.depth + type(node).__name__)
        self.depth += 1
        ast.NodeVisitor.generic_visit(self, node)
        self.depth -= 1
    def visit_Assign(self, node):
        for key, val in ast.iter_fields(node):
            if key == 'targets':
                for subnode in val:
                    crawler = myextract()
                    crawler.visit(subnode)
                    self.targets[".".join(reversed(crawler.stack))] = 1
        self.depth += 1
        ast.NodeVisitor.generic_visit(self, node)
        self.depth -= 1
    def visit_Name(self, node):
        self.names[node.id] = 1
        ast.NodeVisitor.generic_visit(self, node)

def find_fields_in(code, fields_used, names_used):
    myvisitor = myvisitnode()
    t = ast.parse(code)
    myvisitor.visit(t)
    predefines = set(globals().keys()) | set(locals().keys())
    for item in myvisitor.targets.keys():
        if item not in predefines:
            fields_used.add(item)
    definables = set(predefines) | set(myvisitor.targets.keys())
    for item in myvisitor.names.keys():
        if item not in definables:
            names_used.add(item)

def process_selections(data):
    result = []
    if type(data) is list:
        for entry in data:
            if type(entry) is dict:
                for key in entry:
                    result.append([key, entry[key]])
            if type(entry) is list:
                result.append([entry[0], entry[1]])
            elif type(entry) is str or type(entry) is unicode:
                result.append([entry, entry])
    elif type(data) is dict:
        for key, value in sorted(data.items(), key=operator.itemgetter(1)):
            result.append([key, value])
    else:
        raise DAError("Unknown data type in choices selection")
    return(result)

def extract_missing_name(string):
    #logmessage("string was " + str(string))
    m = nameerror_match.search(str(string))
    return m.group(1)
