
function match_forward(text, find, against, start, stop) {
	var count = 1;
	if( stop == null || stop == -1 ) {
		stop = text.length;
	}
	for( var i = start; i < stop; i++ ) {
		if( text.charAt(i) == against )
			count += 1
		else if( text.charAt(i) == find )
			count -= 1
		if( count == 0 )
			return i
	}
	return -1
}
	

var template = function(text, values) {
	// TODO: this could build a post-parsing cache, like the python side does
	//       just store a list of (text,key,text,key,text,...), they will always alternate.
	chunks = text.split(/%[\(\/]/)
	output = [ chunks[0] ]
	for( var i = 1; i < chunks.length; i++) {
		end = match_forward(chunks[i], ')', '(', 0, -1)
		if( end == -1 ) {
			console.log("Template syntax error: unmatched '%(' in chunk starting at: "+chunks[i].substring(0,15))
			return
		}
		key = chunks[i].substring(0,end)
		type = chunks[i][end+1]
		output.push(values[key])
		output.push(chunks[i].substring(end+1))
	}
	return output.join('')
}


