
var template = (function() {

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

	var type_re = /([0-9#0+-]*)\.*([0-9#+-]*)([diouxXeEfFgGcrsqm])(.*)/

	var compile = function (text) {
		var ret = [],
			chunks = text.split(/%[\(\/]/),
			end = -1, i = 1, n = chunks.length
		ret.push(chunks[0])
		for( ; i < n; i++) {
			end = match_forward(chunks[i], ')', '(', 0, -1)
			if( end == -1 )
				return "Template syntax error: unmatched '%(' in chunk starting at: "+chunks[i].substring(0,15)
			key = chunks[i].substring(0,end)
			rest = chunks[i].substring(end)
			match = type_re.exec(rest)
			if( match == null )
				return "Template syntax error: invalid type specifier starting at '"+rest+"'"
			// the |0 operation coerces to a number, anything that doesnt map becomes 0, so "3" -> 3, "" -> 0, null -> 0, etc.
			type = [ match[1]|0, match[2]|0, match[3] ]
			rest = match[4]
			ret.push(key)
			ret.push(type)
			ret.push(rest)
		}
		return ret
	}

	var render = function(text, values) {
		// get the cached compiled version
		var cache = arguments.callee.cache[text] 
			|| (arguments.callee.cache[text] = compile(text)),
			// the first block is always just text
			output = [cache[0]],
			// j is an insert marker into output
			j = 1 // (because .push() is slow on an iphone, but inserting at length is fast everywhere)
			// (and because building up this list is the bulk of what render does)

		// then the rest of the cache items are: [key, format, remainder] triplets
		for( var i = 1, n = cache.length; i < n-2; i += 3) {
			var key = cache[i],
				format = cache[i+1],
				// format has 3 fields: precision, fixed, type
				precision = format[0],
				fixed = format[1],
				type = format[2],
				// the text after the end of the format
				rest = cache[i+2],
				// the value to render for this key
				value = values[key]

			// require the value
			if( value == null ) 
				return "Template missing required value: "+key

			// TODO: the format is used for all kinds of options like padding, etc
			// right now this only really supports %s, %d, and %N.Nf
			// everything else is equivalent to %s
			switch( type ) {
				case 'd':
					output[j++] = "" + parseInt(value)
					break
				case 'f':
					output[j++] = parseFloat(value).toFixed(fixed)
					break
				// output unsupported formats as strings
				// TODO: add support for more types
				case 's':
				default:
					output[j++] = "" + value
			}
			output[j++] = rest
		}
		return output.join('')
	}
	render.cache = {}
	return render

})()

if( console && console.log )
	console.log(template("Hello %(to)s, -%(from)s", {to: "World", from: "Suba"}))
