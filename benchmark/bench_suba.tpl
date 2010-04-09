%(include("_header.html"))
<tbody>
%(n = 0)
%(for item in items:)
	%(n += 1)
	<tr class="%(n % 2 == 0 and 'even' or 'odd')">
	 <td style="text-align: center">%(n)d</td>
	 <td>
		<a href="/stocks/%(item['symbol'])">%(item['symbol'])</a>
	 </td>
	 <td>
		<a href="%(item['url'])">%(item['name'])</a>
	 </td>
	 <td>
		<strong>%(item['price']).2f</strong>
	 </td>
	%(if item['change'] < 0.0:)
	 <td class="minus">%(item['change']).4f</td>
	 <td class="minus">%(item['ratio']).4f</td>
	%(else:)
	 <td>%(item['change']).4f</td>
	 <td>%(item['ratio']).4f</td>
	%/
	</tr>
%/
</tbody>
%(include("_footer.html"))
