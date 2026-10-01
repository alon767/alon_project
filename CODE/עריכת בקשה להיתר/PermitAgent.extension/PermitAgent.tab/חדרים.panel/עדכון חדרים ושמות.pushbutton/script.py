# -*- coding: utf-8 -*-
"""עדכון (או יצירה ראשונה) של חדרים ושמות בקומה של התצוגה הפעילה: מוחק את החדרים והתוויות הקודמים של העובד ובונה מחדש. רק על עותק עבודה (_AGENT). Ctrl+Z אחד מבטל הכול."""
import io
import os
import sys
import datetime
import traceback

TRACE = os.path.join(os.environ['APPDATA'], 'pyRevit', 'permit_write_trace.txt')


def trace(msg):
    with io.open(TRACE, 'a', encoding='utf-8') as f:
        f.write(u'%s  %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), msg))


trace(u'=== start')
try:
    sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib'))
    from Autodesk.Revit.DB import ElementId
    from Autodesk.Revit.UI import TaskDialog
    from System.Collections.Generic import List
    from pyrevit import revit, script, forms
    import permit_write
    trace(u'imports ok')

    doc = revit.doc
    uidoc = revit.uidoc
    view = revit.active_view

    if getattr(view, 'GenLevel', None) is None:
        TaskDialog.Show(u'עדכון חדרים ושמות', u'יש להריץ מתוך תכנית קומה.')
        script.exit()
    if permit_write.WRITE_MARK not in (doc.Title or u''):
        TaskDialog.Show(u'עדכון חדרים ושמות',
                        u'הכלי רץ רק על עותק עבודה.\nשם הקובץ חייב להכיל "%s" (למשל: פרויקט לדוגמא_AGENT.rvt).'
                        % permit_write.WRITE_MARK)
        script.exit()

    SERVICE_NAMES = permit_write.SERVICE_NAMES
    SHAFT_NAMES = permit_write.SHAFT_NAMES
    EDIT, SKIP, CANCEL = u'עריכה...', u'דלג (ללא חדר)', u'ביטול הכול'

    def choose_name(r):
        try:
            uidoc.ShowElements(ElementId(r['id']))
            uidoc.Selection.SetElementIds(List[ElementId]([ElementId(r['id'])]))
            uidoc.RefreshActiveView()        # the room is still temporary (open transaction) - show the highlight
        except Exception:
            pass
        if r['guess'] == permit_write.SHAFT:
            opts = SHAFT_NAMES + SERVICE_NAMES
        else:
            opts = SERVICE_NAMES + SHAFT_NAMES
        msg = u'חלל {} מתוך {}  |  {:.1f} מ"ר  |  הצעה: {}  -  בחר שם לחלל המסומן בתכנית'.format(
            r['_index'], r['_total'], r['area_m2'], r['guess'])
        # office request (Alon, 29.09.26): a vertical list (with a search box), not buttons across
        pick = forms.SelectFromList.show(opts + [EDIT, SKIP, CANCEL], title=msg, button_name=u'בחר',
                                         multiselect=False, width=520, height=640)
        trace(u'room %s -> %s' % (r['id'], pick))
        if pick == CANCEL:
            raise permit_write.Cancelled()
        if pick is None or pick == SKIP:
            return None
        if pick == EDIT:
            txt = forms.ask_for_string(default=r['guess'] if r['guess'] != u'?' else u'',
                                       prompt=u'שם החלל:', title=u'עריכת שם')
            trace(u'room %s edited -> %s' % (r['id'], txt))
            return txt.strip() if txt and txt.strip() else None
        return pick

    def confirm_legacy(count):
        from Autodesk.Revit.UI import TaskDialogCommonButtons, TaskDialogResult
        ans = TaskDialog.Show(
            u'עדכון חדרים ושמות',
            u'נמצאו {} תוויות מהרצה קודמת (לפני שהעובד התחיל לסמן את התוויות שלו).\n'
            u'הן טקסטים של 2.0 מ"מ עם שמות שהעובד מייצר, בתוך החדרים שלו.\n\n'
            u'למחוק אותן כחלק מהעדכון? (אחרת יופיעו שמות כפולים)'.format(count),
            TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No)
        trace(u'legacy labels: %d -> %s' % (count, ans))
        return ans == TaskDialogResult.Yes

    try:
        res = permit_write.run_write(doc, uidoc, view, choose_name, trace, confirm_legacy)
    except permit_write.Cancelled:
        trace(u'cancelled - rolled back')
        TaskDialog.Show(u'עדכון חדרים ושמות', u'בוטל. המודל לא שונה.')
        script.exit()

    try:
        uidoc.Selection.SetElementIds(List[ElementId]())     # the room picked last stays selected otherwise
    except Exception:
        pass
    trace(u'done: %s' % res)
    output = script.get_output()
    output.set_title(u'PermitAgent - עדכון חדרים ושמות')
    if res['removed_rooms'] or res['removed_labels']:
        output.print_md(u'**הוסרו מהרצה קודמת:** {} חדרים, {} תוויות  |  **שמות שנשמרו מבחירה קודמת:** {}'.format(
            res['removed_rooms'], res['removed_labels'], res['reused_names']))
    output.print_md(u'### נוצרו {} חדרים עם שם, {} תוויות טקסט ({})  |  דולגו {}'.format(
        res['created'], res['labels'], res['text_type'], res['skipped']))
    output.print_md(u'**תוויות:** ' + u',  '.join(u'{} ×{}'.format(t, n) for t, n in res['label_counts']))
    if res.get('apartment_summary'):
        output.print_md(u'### מספור דירות (עם כיוון השעון מהצפון-מערב, רציף מלמטה)')
        for line in res['apartment_summary']:
            output.print_md(u'- ' + line)
        if res.get('apartment_labels'):
            output.print_md(u'**בקומה הזו:** ' + u'  |  '.join(res['apartment_labels']))
    if res['errors']:
        output.print_md(u'⚠️ {} תוויות לא נוצרו: {}'.format(len(res['errors']), u' | '.join(res['errors'][:5])))
    output.print_table([[n, u'{:.1f}'.format(a), rule] for n, a, rule in sorted(res['rooms'])],
                       columns=[u'שם', u'שטח מ"ר', u'כלל'])
    output.print_md(u'_כל הפעולה היא Undo אחד - Ctrl+Z מבטל הכול. החדרים מסומנים Comments = PermitAgent._')
except SystemExit:
    raise
except Exception:
    trace(u'ERROR\n' + traceback.format_exc())
    raise
