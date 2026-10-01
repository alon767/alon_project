# -*- coding: utf-8 -*-
"""ניסוי: זיהוי אוטומטי של שמות חדרים בקומה של התצוגה הפעילה, והשוואה לטקסטים הקיימים.
המודל לא משתנה - החדרים הזמניים מבוטלים בסוף (Rollback)."""
import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib'))

from pyrevit import revit, script, forms
import permit_rooms

doc = revit.doc
view = revit.active_view
output = script.get_output()
output.set_title(u'PermitAgent - ניסוי שמות חדרים')

if getattr(view, 'GenLevel', None) is None:
    forms.alert(u'יש לפתוח תכנית קומה (Floor Plan / Area Plan) ולהריץ ממנה.', exitscript=True)

output.print_md(u'**תצוגה:** {}  |  **קומה:** {}'.format(view.Name, view.GenLevel.Name))
output.print_md(u'_החדרים ממוקמים זמנית ומבוטלים בסוף - המודל לא משתנה._')

res = permit_rooms.run_dry(doc, view, log=lambda m: output.print_md(m))
path = permit_rooms.save_result(doc, res)

output.print_md(u'---')
if res['rooms_with_truth_text']:
    pct = 100.0 * res['rooms_matched'] / res['rooms_with_truth_text']
    output.print_md(u'### התאמה לטקסטים שלך: {} מתוך {} ({:.0f}%)'.format(
        res['rooms_matched'], res['rooms_with_truth_text'], pct))
else:
    output.print_md(u'### לא נמצאו טקסטים בתוך החדרים בתצוגה הזו להשוואה')
if res.get('rooms_pending_choice'):
    output.print_md(u'**חדרי שירות שממתינים לבחירת שם מהרשימה:** {} (לא נספרים בציון)'.format(
        res['rooms_pending_choice']))

rows = []
for r in res['rooms']:
    if r.get('pending_choice'):
        mark = u'☐'
    else:
        mark = u'-' if r['match'] is None else (u'✔' if r['match'] else u'✘')
    rows.append([mark, r['guess'] + (u' + ' + u', '.join(r['guess_extras']) if r['guess_extras'] else u''),
                 u' / '.join(r['truth_names']) or u'-', u'{:.1f}'.format(r['area_m2']),
                 r.get('apartment') or u'-', r['rule']])
output.print_table(rows, columns=[u'', u'ניחוש', u'הטקסט שלך', u'שטח מ"ר', u'דירה (זמני)', u'כלל'])
output.print_md(u'**טקסטים שלא נפלו בתוך אף חדר:** {}'.format(len(res['unmatched_texts'])))
output.print_md(u'**דוח מלא:** `{}`'.format(path))
