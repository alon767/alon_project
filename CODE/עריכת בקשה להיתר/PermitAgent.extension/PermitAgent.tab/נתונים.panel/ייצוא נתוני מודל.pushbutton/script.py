# -*- coding: utf-8 -*-
"""ייצוא נתוני המודל לקובץ JSON (קריאה בלבד) — לצורך לימוד סט ההיתר."""
import os
import sys
import time

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib'))

from pyrevit import revit, script, forms
import permit_export

doc = revit.doc
output = script.get_output()
output.set_title(u'PermitAgent - ייצוא נתוני מודל')

if doc.IsFamilyDocument:
    forms.alert(u'יש להריץ בקובץ פרויקט, לא בקובץ משפחה.', exitscript=True)

if doc.PathName:
    out_dir = os.path.join(os.path.dirname(doc.PathName), u'PermitAgent_exports')
else:
    out_dir = os.path.join(os.path.expanduser('~'), 'Desktop', u'PermitAgent_exports')

t0 = time.time()
path, data = permit_export.run_export(doc, out_dir, log=lambda m: output.print_md(m))

output.print_md(u'---')
output.print_md(u'**הייצוא הסתיים** ({:.0f} שניות). המודל לא שונה.'.format(time.time() - t0))
output.print_md(u'- גיליונות: {}  | מבטים: {}  | View Templates: {}'.format(
    len(data['sheets']), len(data['views']), len(data['view_templates'])))
output.print_md(u'- מבטי תכנית שנסרקו: {}  | חדרים: {}  | פתחים: {}'.format(
    len(data['plan_view_annotations']), len(data['rooms']), len(data['openings'])))
output.print_md(u'**קובץ:** `{}`'.format(path))
