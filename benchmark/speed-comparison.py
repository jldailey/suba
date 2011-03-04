#!/usr/bin/env python3.1
import time, os, random, sys
sys.path.insert(0, "..")
import tenjin
from tenjin.helpers import to_str # these must be global for tenjin to work
N = 10000

items = [
			{'symbol':'USD', 'url': 'http://usd/', 'name': 'U.S.D.', 'price': 1.00, 'change': 0.00, 'ratio': 0.5},
			{'symbol':'JAP', 'url': 'http://jap/', 'name': 'J.A.P.', 'price': 2.00, 'change': 1.00, 'ratio': 1.5},
			{'symbol':'CHI', 'url': 'http://chi/', 'name': 'C.H.I.', 'price': 3.00, 'change': 2.00, 'ratio': -.5},
			{'symbol':'USD', 'url': 'http://usd/', 'name': 'U.S.D.', 'price': 1.00, 'change': 0.00, 'ratio': 0.5},
			{'symbol':'JAP', 'url': 'http://jap/', 'name': 'J.A.P.', 'price': 2.00, 'change': 1.00, 'ratio': 1.5},
			{'symbol':'CHI', 'url': 'http://chi/', 'name': 'C.H.I.', 'price': 3.00, 'change': 2.00, 'ratio': -.5},
			{'symbol':'USD', 'url': 'http://usd/', 'name': 'U.S.D.', 'price': 1.00, 'change': 0.00, 'ratio': 0.5},
			{'symbol':'JAP', 'url': 'http://jap/', 'name': 'J.A.P.', 'price': 2.00, 'change': 1.00, 'ratio': 1.5},
			{'symbol':'CHI', 'url': 'http://chi/', 'name': 'C.H.I.', 'price': 3.00, 'change': 2.00, 'ratio': -.5},
			{'symbol':'USD', 'url': 'http://usd/', 'name': 'U.S.D.', 'price': 1.00, 'change': 0.00, 'ratio': 0.5},
			{'symbol':'JAP', 'url': 'http://jap/', 'name': 'J.A.P.', 'price': 2.00, 'change': 1.00, 'ratio': 1.5},
			{'symbol':'CHI', 'url': 'http://chi/', 'name': 'C.H.I.', 'price': 3.00, 'change': 2.00, 'ratio': -.5},
			{'symbol':'USD', 'url': 'http://usd/', 'name': 'U.S.D.', 'price': 1.00, 'change': 0.00, 'ratio': 0.5},
			{'symbol':'JAP', 'url': 'http://jap/', 'name': 'J.A.P.', 'price': 2.00, 'change': 1.00, 'ratio': 1.5},
			{'symbol':'CHI', 'url': 'http://chi/', 'name': 'C.H.I.', 'price': 3.00, 'change': 2.00, 'ratio': -.5},
			{'symbol':'USD', 'url': 'http://usd/', 'name': 'U.S.D.', 'price': 1.00, 'change': 0.00, 'ratio': 0.5},
			{'symbol':'JAP', 'url': 'http://jap/', 'name': 'J.A.P.', 'price': 2.00, 'change': 1.00, 'ratio': 1.5},
			{'symbol':'CHI', 'url': 'http://chi/', 'name': 'C.H.I.', 'price': 3.00, 'change': 2.00, 'ratio': -.5},
			{'symbol':'USD', 'url': 'http://usd/', 'name': 'U.S.D.', 'price': 1.00, 'change': 0.00, 'ratio': 0.5},
			{'symbol':'JAP', 'url': 'http://jap/', 'name': 'J.A.P.', 'price': 2.00, 'change': 1.00, 'ratio': 1.5},
]

def suba_test(N):
	from suba import template # count the one-time import cost
	for i in range(N):
		ret = ''.join(template(filename="bench_suba.tpl", base_path=".", stripWhitespace=False, 
			items = items, name="Suba"))

def tenjin_test(N):
	engine = tenjin.Engine(cache=True, path=['.']) # count the one time creation cost of the engine
	for i in range(N):
		ret = engine.render('bench_tenjin.pyhtml', { 'list': items, 'name': "Tenjin" })
	elapsed = (time.time() - start)
	os.unlink('./bench_tenjin.pyhtml.cache')
	os.unlink('./_header.html.cache')
	os.unlink('./_footer.html.cache')

def evoque_test(N):
	from evoque.template import Template
	t = Template(os.path.sep.join([os.getcwd(), "."]), "bench_evoque.html", quoting="str")
	for _ in range(N):
		(t.evoque({ 'items': items, 'name': "Evoque" }))

tests = [suba_test, tenjin_test]#, evoque_test ]

t = random.randint(0,len(tests))
for i in range(len(tests)):
	start = time.time()
	test = tests[(t+i)%len(tests)]
	test(N)
	elapsed = (time.time() - start)
	print("%s: %.2f pages/sec (in %.2f seconds)" % (test.__name__, (N/elapsed), elapsed))

