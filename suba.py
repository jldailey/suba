"""
	Fast template engine, does very simple parsing (no regex, one split) and then generates the AST tree directly.
	The AST tree is compiled to bytecode and cached (so only the first run of a template must compile).
	The bytecode cache is in-memory only.
"""
import re, io, os, ast, builtins
from ast import *

__all__ = ['template', 'buffered']

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
	def __init__(self, stripWhitespace=False, encoding=None, locals=None):
		ast.NodeTransformer.__init__(self)
		self.seenStore = {}
		self.seenFuncs = {}
		self.encoding = encoding
		self.stripWhitespace = stripWhitespace
		self.locals = locals if locals is not None else []
	def visit_Expr(self, node):
		if type(node.value) != Yield: # if there is a bare expression (that would get ignored), such as %(name), then Yield it instead
			new = Yield(value=node.value)
			node.value = ast.copy_location(new, node.value)
		if type(node.value.value) == Str:
			if self.stripWhitespace:
				s = strip_whitespace(node.value.value.s)
				if len(s) == 0:
					return None # dont even compile in the yield if it was only yielding white space
				else:
					node.value.value.s = s
		else: # any yield that isn't yielding a string already, gets wrapped to produce one
			new = Call(func=Name(id='str', ctx=Load()), args=[node.value.value], keywords=[], starargs=None, kwargs=None)
			node.value.value = ast.copy_location(new, node.value.value)
		self.generic_visit(node.value)
		return node
	def visit_FunctionDef(self, node):
		self.seenFuncs[node.name] = True
		for arg in node.args.args:
			self.seenStore[arg.arg] = True
		self.generic_visit(node)
		return node
	def visit_Call(self, node):
		if type(node.func) is Name and self.seenFuncs.get(node.func.id, False) is not False: # if we are calling a function defined locally in the template
			new = Call(func=Attribute(value=Str(s=''), attr='join', ctx=Load()), args=[node], keywords=[], starargs=None, kwargs=None)
			self.generic_visit(node)
			return ast.copy_location(new, node)
		else:
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
				if builtins.__dict__.get(node.id,None) is None and self.seenFuncs.get(node.id,None) is None:
					new = Subscript( # replace the variable with a reference to args['...']
						value=Name(id='args', ctx=Load()),
						slice=Index(value=Str(s=node.id)), ctx=node.ctx, lineno=node.lineno)
					return ast.copy_location(new, node)
			return node
		else: # is Load, but a local variable
			return node

def strip_whitespace(s):
	out = io.StringIO()
	remove = False
	for c in s:
		if c == "\n":
			remove = True
		if remove and c not in ("\n", "\t", " "):
			remove = False
		if not remove:
			out.write(c)
	return out.getvalue()
	

_code_cache = {}
_include_cache = {}
def include(filename, base_path):
	if _include_cache.get(filename,None) is None:
		_include_cache[filename] = strip_whitespace(open(os.path.sep.join(base_path + [filename])).read())
	return _include_cache[filename]

def buffered(gen):
	return ''.join(gen)

type_re = re.compile("[0-9.#0+ -]*[diouxXeEfFgGcrsq]")

def template(text=None, filename=None, stripWhitespace=False, encoding="utf8", base_path=".", **kw):
	"""
		Fast template engine, does very simple parsing and then generates the AST tree directly.
		The AST tree is compiled to bytecode and cached (so only the first run of a template must compile).
		The code cache is in-memory only.
	
		The most basic syntax is similar to the % string substitution operator, but without the trailing type indicator.
		The template itself returns a generator, so you must read it out with something that will iterate it.
		Typically, one would just join() it all, but you could also flush each block directly if you wanted.

		>>> ''.join(template(text="<p>%(name)s</p>", name="John"))
		'<p>John</p>'

		>>> with open("_test_file_", "w") as f:
		...		f.write("<p>%(name)s</p>")
		15
		>>> ''.join(template(filename="_test_file_", name="Jacob"))
		'<p>Jacob</p>'
		>>> os.unlink("_test_file_")
		
		The more advanced syntax is just embedded python, with one rule for handling indents:
		 - Lines that end in ':' increase the indent of all following statements until a %/ is reached.

		>>> ''.join(template(text=\"""
		...	<ul>
		...	%(for item in items:)
		...		<li>%(item)s</li>
		...	%/
		...	</ul>""\", items=["John", "Paul", "Ringo"], stripWhitespace=True))
		...
		'<ul><li>John</li><li>Paul</li><li>Ringo</li></ul>'

		>>> import datetime
		>>> ''.join(template(text="now is: %(datetime.datetime.strptime('12/10/2001','%d/%m/%Y').strftime('%d/%m/%y'))s", datetime=datetime))
		'now is: 12/10/01'

		>>> pi = 3.1415926
		>>> ''.join(template(text="pi is about %(pi)d %(pi).2f %(pi).4f", pi=pi))
		'pi is about 3 3.14 3.1416'

		>>> value = '"Halt!"'
		>>> ''.join(template(text="%(value)q, the guard shouted.", value=value))
		'\\\\"Halt!\\\\", the guard shouted.'

	"""

	if text is None and filename is not None:
		h = filename.__hash__()
		try:
			h += os.path.getmtime(filename)
		except:
			pass
	elif filename is None and text is not None:
		h = text.__hash__()
	else:
		raise ArgumentError("template() requires either text= or filename= arguments.")
	base_path = base_path.split(os.path.sep)
	
	## Compile Phase ##
	# note about performance: compiling time is one-time only, so on scale it matters very very little.
	# what matters is the execution of the generated code.
	# absolutely anything that can be done to manipulate the generated AST to save execution time should be done.
	if _code_cache.get(h, None) is None:
		if filename is not None:
			text = open(os.path.sep.join(base_path + [filename]), "rb").read()
		if type(text) is bytes:
			text = str(text, encoding)
		head = Module(body=[ # build the first node of the new code tree
			# which will be a module with a single function: 'execute', a generator function
			FunctionDef(name='execute', args=arguments(args=[], vararg=None, varargannotation=None, kwonlyargs=[], kwarg='args', kwargannotation=None, defaults=[], kw_defaults=[]), 
				body=[], decorator_list=[], returns=None),
			])
		# point a cursor into the tree where we will build from
		# the cursor is a stack, so cursor[-1] is the current location
		cursor = []
		cursor.append(head.body[0].body) # this is the body of the 'execute' function
		# split up the text into chunks
		chunks = text.split('%')
		c = 0
		while True:
			if c >= len(chunks) - 1: break # force re-eval of len()
			chunka = chunks[c]
			chunkb = chunks[c + 1]
			if chunkb[0] not in ('(','/'):
				chunks[c] = chunka + '%' + chunkb
				del chunks[c+1]
			else:
				c += 1
		lineno = 1 # we keep track of this as best we can, so that stack trace rendering works
		for c in range(len(chunks)):
			chunk = chunks[c]
			if len(chunk) == 0: continue
			if chunk[0] == '(':
				i = match_forward(chunk, ')', '(', start=1)
				# if we found a matched parentheses group %(...)...
				# then eval the middle, and yield the left overs
				if i == -1:
					raise TemplateFormatError("Unmatched '%%(' in template, beginning at: '%s'" % (chunk[0:50]))
				eval_part = chunk[1:i]
				text_part = chunk[i+1:]
				type_part = None
				m = type_re.match(text_part)
				if m is not None:
					type_part = m.group(0)
					text_part = text_part[len(type_part):]
				do_descend = False
				if eval_part.endswith(":"): # if the statement to eval is like an if, while, or for, then we need to do some tricks
					eval_part += " pass" # add a temp. node, so we can parse the incomplete statement
					do_descend = True
				if eval_part.startswith("else:"):
					# for an else statement, just move the cursor back and over to the orelse block
					cursor[-1] = cursor[-2][-1].orelse
				else:
					if eval_part.startswith("elif "):
						cursor[-1] = cursor[-2][-1].orelse
						eval_part = eval_part[2:] # and add the if statement
						do_descend = True
					try: # parse the body of the %( ... ) group
						node = ast.parse(eval_part).body[0]
					except IndentationError as e: # fix up indentation errors to make sure they indicate the right spot in the actual template file
						e.filename = filename
						e.lineno += lineno - eval_part.count("\n")
						e.offset += 2 # should be 2 + (space between left margin and opening %), but i dont know how to count this atm
						raise
					except Exception as e:
						print("Error while parsing sub-expression: %s" % (eval_part))
						raise e
					
					# update all the line numbers
					# for child in ast.walk(node):
						# child.lineno = lineno

					# if this eval_part had a type_part attached (a type specifier as recognized by the % operator)
					# then wrap the node in a call to the % operator with this type specifier
					if type_part is not None:
						if type_part == 'q':
							new = Expr(value=Call(func=Attribute(value= node.value, attr='replace', ctx=Load(lineno=lineno), lineno=lineno), args=[Str(s="\"", lineno=lineno),Str(s="\\\"", lineno=lineno)], keywords=[], starargs=None, kwargs=None, lineno=lineno), lineno=lineno)
							node = ast.copy_location(new, node.value)
						else:
							new = Expr(value=BinOp(left=Str(s='%'+type_part, lineno=lineno), op=Mod(lineno=lineno), right=node.value, lineno=lineno), lineno=lineno)
							node = ast.copy_location(new, node.value)

					# put our new node into the ast tree
					cursor[-1].append(node)
					if do_descend: # adjust the cursor is needed
						del cursor[-1][-1].body[0] # clear the temp. node from this new block
						cursor.append(cursor[-1][-1].body) # and point our cursor inside the new block
				if len(text_part): # if there is left over text after the %( ... ) block, yield it out.
					cursor[-1].append(Expr(value=Yield(value=Str(s=text_part, lineno=lineno), lineno=lineno), lineno=lineno))
			elif chunk[0] == '/': # process a %/ block terminator, by decreasing the indent
				if len(cursor) < 2:
					raise TemplateFormatError("Too many close tags %/")
				# pop the right side off the cursor stack
				cursor = cursor[:-1]
				if len(chunk) > 1:
					cursor[-1].append(Expr(value=Yield(value=Str(s=chunk[1:], lineno=lineno), lineno=lineno), lineno=lineno))
			else:
				cursor[-1].append(Expr(value=Yield(value=Str(
					s=("%"+chunk) if c > 0 else chunk, lineno=lineno), lineno=lineno), lineno=lineno))
			lineno += chunk.count("\n")

		# patch up the generated tree, to reference the keyword arguments when necessary
		head = TemplateTransformer(stripWhitespace, encoding, ('include',)).visit(head)
		# patch up all the book-keeping of indents and such
		ast.fix_missing_locations(head)
		# print(ast.dump(head))
		# sys.exit(0)
		# print("compiling.")
		if filename is None:
			filename = "template_%d" % text.__hash__()
		co = compile(head,filename,"exec")
		_code_cache[h] = co

	## Execution Phase ##
	# provide a few global helpers and then execute the cached byte code
	loc = {}
	glob = {'include':lambda f: include(f, base_path)}
	exec(_code_cache[h], glob, loc)
	gen = loc['execute'](**kw)
	return gen


if __name__ == "__main__":
	import doctest
	doctest.testmod(raise_on_error=False)
