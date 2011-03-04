#!/usr/bin/env python3.1
import os, sys
sys.path.insert(0,"..")
from suba import template

test_to_run = None
if len(sys.argv) > 1:
	test_to_run = sys.argv[1]

for file in os.listdir("."):
	if file.endswith(".test"):
		if test_to_run is None or file.startswith(test_to_run):
			try:
				output = ''.join(template(filename=file, root=".", stripWhitespace=True, names = ['John','Paul','Ringo']))
			except Exception as e:
				output = str(e)
				if test_to_run is not None:
					raise
			correct = open(os.path.sep.join([".",file.replace(".test",".output")]), "r").read()[:-1]
			if output != correct:
				print(file,"FAIL:")
				print("EXPECTED:")
				print(correct)
				print("GOT:")
				print(output)
			else:
				print(file, "PASS.")
