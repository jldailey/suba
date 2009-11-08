from suba import template
import os

for file in os.listdir("test"):
	if file.endswith(".test"):
		output = ''.join(template(filename=os.path.sep.join(["test",file]), stripWhitespace=True, names = ['John','Paul','Ringo']))
		correct = open(os.path.sep.join(["test",file.replace(".test",".output")]), "r").read()[:-1]
		if output != correct:
			print(file,"FAIL:")
			print("EXPECTED:")
			print(correct)
			print("GOT:")
			print(output)
		else:
			print(file, "PASS.")
