from suba import template
import os, sys

test_to_run = None
if len(sys.argv) > 1:
	test_to_run = sys.argv[1]

for file in os.listdir("test"):
	if file.endswith(".test"):
		if test_to_run is None or file.startswith(test_to_run):
			try:
				output = ''.join(template(filename=file, base_path="test", stripWhitespace=True, names = ['John','Paul','Ringo']))
			except Exception as e:
				output = str(e)
				if test_to_run is not None:
					raise
			correct = open(os.path.sep.join(["test",file.replace(".test",".output")]), "r").read()[:-1]
			if output != correct:
				print(file,"FAIL:")
				print("EXPECTED:")
				print(correct)
				print("GOT:")
				print(output)
			else:
				print(file, "PASS.")
