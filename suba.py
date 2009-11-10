"""
	Fast template engine, does very simple parsing (no regex, one split) and then generates the AST tree directly.
	The AST tree is compiled to bytecode and cached (so only the first run of a template must compile).
	The bytecode cache is in-memory only.
"""
import re, io, os, ast
from ast import *

__all__ = ['template']

class TemplateFormatError(Exception): pass

def gen_bytes(gen, encoding):
	for item in gen:
		yield bytes(str(item), encoding)

def match_forward(text, find, against, start=0, stop=-1):
	count = 1
	if stop == -1:
		stop = len(text)
	for i in range(start,stop):
		if text[i] == against:
			count += 1
		elif text[i] == find:
			count -= 1
		if count == 0:
			return i
	return -1

class TemplateTransformer(ast.NodeTransformer):
	def __init__(self, base_path=".", stripWhitespace=False, encoding=None, locals=None):
		ast.NodeTransformer.__init__(self)
		self.base_path = base_path
		self.seenStore = {}
		self.seenFuncs = {}
		self.encoding = encoding
		self.stripWhitespace = stripWhitespace
		self.locals = locals if locals is not None else []
	def visit_Expr(self, node):
		if type(node.value) != Yield: # if there is a bare expression (that would get ignored), such as %(name), then Yield it instead
			node.value = Yield(value=node.value)
		# print("visit_Yield: %s" % str(ast.dump(node.value)))
		# print("stripping whitespace...", self.stripWhitespace and type(node.value.value) == Str)
		# print("type(node.value.value) %s" % str(type(node.value.value)))
		# try: 
			# print("type(node.value.value.func) %s" % str(type(node.value.value.func)))
			# print("type(node.value.value.func.id) %s" % str(node.value.value.func.id))
		# except AttributeError:
			# pass
		# try: 
			# print("type(node.value.value.func.value) %s" % str(type(node.value.value.func.value)))
		# except AttributeError:
			# pass
		if type(node.value.value) == Str:
			if self.stripWhitespace:
				s = strip_whitespace(node.value.value.s)
				if len(s) == 0:
					# print("returning None")
					return None # dont even compile in the yield if it was only yielding white space
				else:
					# print("setting new value")
					node.value.value.s = s
		elif type(node.value.value) == Call and type(node.value.value.func) is Name and self.seenFuncs.get(node.value.value.func.id, None) is not None:
			# print("Not wrapping local function")
			pass
		else: # any yield that isn't yielding a string already, gets wrapped to produce one
			# print("Wrapping expresion with str()")
			node.value.value = Call(func=Name(id='str', ctx=Load()), args=[node.value.value], keywords=[], starargs=None, kwargs=None)
		self.generic_visit(node.value)
		return node
	def visit_FunctionDef(self, node):
		# print("remembering function %s" % node.name)
		# multiple includes of the same file can cause it's generator function to be inlined in the ast more than once
		# so let's filter those out
		if self.seenFuncs.get(node.name, False) is True:
			# print("Discarding duplicate FunctionDef: %s" % node.name)
			return None
		self.seenFuncs[node.name] = True
		for arg in node.args.args:
			# print("remembering argument %s" % arg.arg)
			self.seenStore[arg.arg] = True
		self.generic_visit(node)
		return node
	# def visit_Subscript(self, node): # do not descend into Subscripts on purpose
		# return node
	def visit_Call(self, node):
		# print("Call visiting: %s" % ast.dump(node.func))
		if type(node.func) is Name and self.seenFuncs.get(node.func.id, False) is not False: # if we are calling a function defined locally in the template
			new = Call(func=Attribute(value=Str(s=''), attr='join', ctx=Load()), args=[node], keywords=[], starargs=None, kwargs=None)
			self.generic_visit(node)
			return ast.copy_location(new, node)
		else:
			# print("Call skipping")
			pass
		self.generic_visit(node)
		return node
	def visit_Name(self, node):
		if type(node.ctx) == ast.Store:
			self.seenStore[node.id] = True
			return node
		elif type(node.ctx) == ast.Load and self.seenStore.get(node.id, False) is False:
			try:
				self.locals.index(node.id)
			except ValueError: # if it's not one of the pre-defined locals
				if node.id is not 'args' and __builtins__.get(node.id,None) is None and self.seenFuncs.get(node.id,None) is None:
					return Subscript( # replace the variable with a reference to args['...']
						value=Name(id='args', ctx=Load()),
					slice=Index(value=Str(s=node.id)), ctx=node.ctx)
			return node
		else: # is Load, but a local variable
			return node

def strip_whitespace(s):
	out = io.StringIO()
	remove = False
	for c in s:
		# print("check: %s" % repr(c))
		if c == "\n":
			remove = True
		if remove and c not in ("\n", "\t", " "):
			remove = False
		if not remove:
			out.write(c)
	return out.getvalue()

_code_cache = {}
_include_cache = {}
_mtime_cache = {}

def buffered(gen):
	return ''.join(gen)

def template(text=None, filename=None, stripWhitespace=False, encoding="utf8", base_path=".", **kw):
	"""
		Fast template engine, does very simple parsing (no regex, one split) and then generates the AST tree directly.
		The AST tree is compiled to bytecode and cached (so only the first run of a template must compile).
		The code cache is in-memory only.
	
		The most basic syntax is similar to the % string substitution operator, but without the trailing type indicator.
		The template itself returns a generator, so you must read it out with something that will iterate it.
		Typically, one would just join() it all, but you could also flush each block directly if you wanted.

		>>> ''.join(template(text="<p>%(name)</p>", name="John"))
		'<p>John</p>'

		>>> with open("_test_file_", "w") as f:
		...		f.write("<p>%(name)</p>")
		>>>	''.join(template(filename="_test_file_", name="Jacob"))
		'<p>Jacob</p>'
		>>> os.unlink("_test_file_")
		
		The more advanced syntax is just embedded python, with one rule for handling indents:
		 - Lines that end in ':' increase the indent of all following statements until a %/ is reached.

		>>> ''.join(template(text=\"""
		...	<ul>
		...	%(for item in items:)
		...		<li>%(item)</li>
		...	%/
		...	</ul>""\", items=["John", "Paul", "Ringo"])).replace('\n','').replace('\t','')
		...
		'<ul><li>John</li><li>Paul</li><li>Ringo</li></ul>'
	"""

	base_path = base_path.split(os.path.sep)

	if text is None and filename is not None:
		h = filename.__hash__()
		filename = os.path.sep.join(base_path + [filename])
	elif filename is None and text is not None:
		h = text.__hash__()
	else:
		raise ArgumentError("template() requires either text= or filename= arguments.")
	
	## Compile Phase ##
	# note about performance: compiling time is one-time only, so on scale it matters very very little.
	# what matters is the execution of the generated code.
	# absolutely anything that can be done to manipulate the generated AST to save execution time should be done.
	if _code_cache.get(h, None) is None\
		or (filename is not None and _mtime_cache.get(filename,0) < os.path.getmtime(filename)):
		if filename is not None:
			text = open(filename, "rb").read()
		if type(text) is bytes:
			text = str(text, encoding)

		# as the generator returns nodes, it indicates how the cursor should advance
		NO_MOTION = 0 # similar to a line that ends with ;
		DESCEND = 1  # like an if:, for:
		ELSE_DESCEND = 2 # like an else:
		ELIF_DESCEND = 3 # like an elif:
		UNDESCEND = 4 # any %/

		def generate_ast(text):
			""" Parses text, yielding [ast node, motion] pairs """
			lineno = 1
			mark = 0
			while mark < len(text):
				i = text.find('%',mark)
				# print("i: %d"% i)
				if i == -1:
					# print("end of line: %s" % text[mark:])
					yield Expr(value=Yield(value=Str(s=text[mark:], lineno=lineno), lineno=lineno), lineno=lineno), NO_MOTION
					mark = len(text)
				else:
					lineno += text.count('\n',mark,i)
					c = text[i+1]
					if c == '(':
						end = match_forward(text, ')', '(', start=i+2)
						if end == -1:
							raise TemplateFormatError("Unmatched '%%(' in template, beginning at: '%s'" % (text[i:i+15]))
						text_part = text[mark:i]
						eval_part = text[i+2:end]
						node = None
						motion = NO_MOTION
						if stripWhitespace:
							text_part = text_part.replace('\n','').replace('\t','')
						if len(text_part) > 0:
							# print("text part: %s" % text_part)
							yield Expr(value=Yield(value=Str(s=text_part, lineno=lineno), lineno=lineno), lineno=lineno), motion
						# print("eval part: %s" % eval_part)
						if eval_part.endswith("else:"):
							motion = ELSE_DESCEND
						else:
							if eval_part.endswith(':'):
								motion = DESCEND
								eval_part += " pass"
							if eval_part.startswith("elif "):
								motion = ELIF_DESCEND
								eval_part = eval_part[2:] # chop the 'el' off the front, that part of it is captured by the motion
							try:
								node = ast.parse(eval_part).body[0]
							except Exception as e:
								e.filename = filename
								e.lineno += lineno - eval_part.count("\n")
								e.offset += 2 # should be 2 + (space between left margin and opening %), but i dont know how to count this atm
								raise
							for child in ast.walk(node):
								child.lineno = lineno
						if type(node) is Expr and type(node.value) is Call and type(node.value.func) is Name \
							and node.value.func.id is 'include':
							argf = node.value.args[0].s
							for node in get_include(argf):
								# print("about to yield from include: %s" % (ast.dump(node)))
								yield node, NO_MOTION
						else:
							# print("about to yield: %s, %s" % (ast.dump(node), motion))
							yield node, motion
						mark = end+1
					elif c == '/':
						if i-mark > 0:
							yield Expr(value=Yield(value=Str(s=text[mark:i], lineno=lineno), lineno=lineno), lineno=lineno), NO_MOTION
						yield None, UNDESCEND
						mark = i+2
					else:
						yield Expr(value=Yield(value=Str(s=text[mark:i+1], lineno=lineno), lineno=lineno), lineno=lineno), NO_MOTION # +1 so that we include the % that find() found
						mark = i+1

		def compile_ast(text):
			head = Module(body=[ # build the first node of the new code tree
				FunctionDef(name='execute', args=arguments(args=[], vararg=None, varargannotation=None, kwonlyargs=[], kwarg='args', kwargannotation=None, defaults=[], kw_defaults=[]), 
					body=[], decorator_list=[], returns=None),
				])
			cursor = []
			cursor.append(head.body[0].body) # the body of the first function
			# read out the results of the generator
			for node, motion in generate_ast(text):
				# print("got node,motion: %s, %d" % (ast.dump(node) if node else None, motion))

				# on an else if block, first we side-step, then descend normally
				if motion is ELIF_DESCEND:
					cursor[-1] = cursor[-2][-1].orelse
					motion = DESCEND
				# all nodes get inserted at the end of the current cursor
				if node is not None:
					cursor[-1].append(node)
				# adjust cursor based on motion yielded
				if motion is NO_MOTION:
					pass
				elif motion is DESCEND:
					del cursor[-1][-1].body[0]
					cursor.append(cursor[-1][-1].body)
				elif motion is ELSE_DESCEND:
					cursor[-1] = cursor[-2][-1].orelse
				elif motion is UNDESCEND:
					if len(cursor) < 2:
						raise TemplateFormatError("Too many close tags %/")
					cursor = cursor[:-1]
			return head

		def get_include(filename):
			""" Includes are compiled to ast, cached, then inlined into the larger ast. """
			filename = os.path.sep.join(base_path + [filename])
			if _include_cache.get(filename, None) is None:
				# print("Filling include cache: %s" % filename)
				fundef = compile_ast(open(filename).read()).body[0]
				# print("Got fundef: %s %s" % (str(fundef), fundef.name))
				fundef.name = filename.split(os.path.sep)[-1].replace('.','_')
				# print("Mangled to: %s" % (fundef.name))
				_include_cache[filename] = [fundef, Expr(value=Call(func=Name(id=fundef.name, ctx=Load()), args=[], keywords=[], starargs=None, kwargs=Name(id='args', ctx=Load())))]
				# print("Cached as: %s\n%s" % (ast.dump(_include_cache[filename][0]), ast.dump(_include_cache[filename][1])))
			return _include_cache[filename]

		head = compile_ast(text)
		# print(ast.dump(head))

		# patch up the generated tree, to reference the keyword arguments when necessary, among other things
		head = TemplateTransformer(stripWhitespace, encoding).visit(head)
		# patch up all the book-keeping of indents and such, if any were missed
		ast.fix_missing_locations(head)
		# print(ast.dump(head))
		if filename is None:
			filename = "template_%d" % text.__hash__()
		co = compile(head,filename,"exec")
		_code_cache[h] = co

	## Execution Phase ##
	# provide a few global helpers and then execute the cached byte code
	loc = {}
	glob = {}
	exec(_code_cache[h], glob, loc)
	gen = loc['execute'](**kw)
	return gen

if __name__ == "__main__":
	print(''.join(template(text="""Hello %("world")!""")))

