README.md: README.template.md parameter_descriptions.py Makefile
	perl -pe '/^#execute *(\s.*)/ and $$_ = `$$1`' $< >$@
