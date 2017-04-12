import textwrap

def format_description(issue, indent=0):
	description = issue.description.replace('\r', '\n')
	paragraphs = description.split('\n\n')
	formatted_description = '\n\n'.join([textwrap.fill(paragraph,
			width=72,
			initial_indent=' ' * indent,
			subsequent_indent=' ' * indent,
			replace_whitespace=False)
			for paragraph in paragraphs])
	return formatted_description

# move _parse_issue_file

