import os, re, sys

# these valkues need to updated every session
course_name = 'Software Construction'
postgraduate_course_code = 'COMP9041'
course_forum_url = 'https://piazza.com/class/j5ji4vjjra62a3'
lecture_recordings_url = 'https://moodle.telt.unsw.edu.au/course/view.php?id=27708'

# these values are only used if they can't be extracted from sys.argv[0]
base_dir = '/web/cs1511'
course_account = 'cs1511'
unsw_session = '19T1'

full_pathname =  os.path.realpath(sys.argv[0])
dir = os.path.dirname(full_pathname)
m = re.search(r'^(.*\b([a-z][a-z]\d{4})(cgi)?\b.*\b(\d\d[scxT]\d)\b).*', dir)
if m:
    (base_dir, course_account, unsw_session) = (m.group(1), m.group(2),  m.group(4))
else:
    m = re.search(r'^(.*\b([a-z][a-z]\d{4})(cgi)?(/public_html)?)\b.*', dir)
    if m:
        (base_dir, course_account, unsw_session) = (m.group(1), m.group(2), unsw_session)

home_pathname = re.sub('/tmp_amd/\w+/export/\w+/\d/(\w+)', r'/home/\1', base_dir)
web_pathname = re.sub('/tmp_amd/\w+/export/\w+/\d/(\w+)/public_html', r'/web/\1', base_dir)
if os.path.exists(home_pathname):
    base_dir = home_pathname
elif os.path.exists(web_pathname):
    base_dir = web_pathname
#debug = os.environ.get('DEBUG', 0)
#if debug: print(full_pathname, '->',  (base_dir, course, unsw_session))

course_code = course_account
for (short,full) in {'cs':'COMP', 'en':'ENGG', 'se':'SENG'}.items():
    if course_code.startswith(short):
        course_code = course_code.replace(short, full)
        break

course_configuration = {
    'base_directory' : base_dir,
    'scripts_directory' : os.path.join(base_dir, 'scripts'),
    'bin_directory' : os.path.join('/home', course_account , 'bin'),
    'work_directory' : os.path.join(base_dir , 'work'),
    'out_directory' : os.path.join(base_dir , 'work', '.out'),
    'marked_directory' : os.path.join(base_dir , 'work', '.out', '.marked'),
    'tlb_directory' : os.path.join(base_dir, 'tlb'),
    'lecture_directory' : os.path.join(base_dir, 'lec'),
    'sms_directory' : os.path.join(base_dir, 'work', unsw_session+'db.sms'),
    'course_account' : course_account,
    'course_code' : course_code,
    'postgraduate_course_code' : postgraduate_course_code,
    'course_codes' : [course_code, postgraduate_course_code], # including aliases
    'course_name' : course_name,
    'unsw_session' : unsw_session,
    'course_forum_url': course_forum_url,
    'lecture_recordings_url': lecture_recordings_url,
}
# /home/class/bin needed so this wworks in exam environment
course_configuration['PATH'] =  ':'.join([course_configuration['scripts_directory'], course_configuration['bin_directory'],  '/home/give/stable/bin', '/usr/local/bin', '/usr/bin', '/bin', '/sbin', '/usr/sbin', '/home/class/bin', '.'])
