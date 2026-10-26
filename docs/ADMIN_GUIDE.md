# Administrator guide — دليل مدير النظام

Who can do what here: the **System Administrator** manages users and technical settings. The **Dean and Vice-Dean** can establish committees, change policy settings and manage the MOM template. Everything below is under **Administration & Settings** (الإدارة والإعدادات).

## 1. Users and school roles — المستخدمون والأدوار

*Administration → Users and school roles*

Each account has one **school role**:

| School role | Meaning |
|---|---|
| Dean — العميد | Full visibility and approval rights across all committees |
| Vice-Dean — وكيل الكلية | Same as the Dean (final MOM approval, urgent tasks, verification, follow-up) |
| System Administrator — مدير النظام | Users, committees, settings, audit log, data administration |
| Faculty / Staff — عضو هيئة تدريس / موظف | Sees only the committees they belong to |

**Committee roles** (Chair, Secretary, Member) are *not* set on the user. They are set per committee from the committee's **Members** tab, so one person can chair one committee and be a member of another.

* **New user:** username, Arabic and English names, e-mail, school role. The initial password is `ChangeMe@2026` unless you type one, and the user must change it at first login.
* **Reset a password:** open the user, type a new password and save. The user must change it again at the next login.
* **Leaving staff:** untick **Active**. Their history stays, but they can no longer log in.

## 2. Committees and membership — اللجان والعضوية

*Committees → Establish committee* (or **Edit** on a committee)

* **Code:** a short code used in decision IDs (`DEC-2627-SAE-001`). Keep it at 8 characters or fewer.
* **Formation reference:** the council decision or letter that formed the committee. Upload the formation document itself in the archive under *Committee Formation & Membership*.
* **Regular meetings:** frequency, week of the month (1st to 4th, or last), weekday, time and venue. Once set, the committee's **Meetings** tab can generate the whole year's regular meetings in one click.
* **Reporting:** monthly, semester, or after the midterm results.
* **Members tab:** add a person with a role. Changing a role closes the old membership and opens a new one, so the history keeps who held which role and when. A new chair or secretary automatically demotes the previous one to member. **End** closes a membership without deleting it.
* **Responsibilities** and **Annual action plan** are kept per committee. A plan item can be converted into a task.

> The 12 school committees and their members were loaded from the official formation list. Meeting days and secretaries were **not** in that list, so set them for each committee. Until a secretary is set, the chair handles the minutes.

## 3. The approved MOM template — نموذج محضر الاجتماع المعتمد

*Administration → Approved MOM template*

The built-in template reproduces the school's MOM form, including the header, logo and page border. Each meeting page offers **تحميل نموذج محضر الاجتماع المعتمد — Download Approved MOM Template**, already filled with that meeting's data.

To use a revised form, put the placeholders below into the Word file and upload it. Uploading activates the new version, and earlier versions stay available and can be re-activated.

| Placeholder | Filled with |
|---|---|
| `{{committee_name}}` | Committee name (Arabic) |
| `{{meeting_number}}` `{{year}}` | Meeting number and academic year |
| `{{date}}` `{{day}}` `{{time}}` `{{venue}}` | Date, weekday, time, venue |
| `{{meeting_type}}` | Regular / exceptional / urgent |
| `{{chair}}` `{{secretary}}` | Names |
| `{{absentees}}` | Absent / excused members, if attendance was recorded |
| `{{issue_date}}` | Date the template was generated |
| `{{attendees_table}}` * | Table of members and roles with a signature column |
| `{{agenda}}` * | Numbered agenda |
| `{{previous_actions_table}}` * | Open decisions from earlier meetings and their status |
| `{{discussion}}` * | Space for the discussion summary |
| `{{decisions_table}}` * | Table: decision / responsible / deadline / required evidence |

\* Put these alone on their own line; they are replaced by tables or lists.

## 4. Policy settings and KPI weights — إعدادات السياسات وأوزان المؤشرات

*Administration → Policy settings*

| Setting | Default | Effect |
|---|---|---|
| Days allowed to submit a MOM after the meeting | 7 | The MOM becomes *overdue* after this, and counts in MOM compliance |
| MOMs need management approval after the chair | On | Route: chair → Vice-Dean/Dean. Turn off for chair-only approval |
| Send e-mail notifications | Off | Turn on once SMTP is configured (see DEPLOYMENT.md) |
| Remind members N days before a meeting | 2 | |
| Warn N days before a task deadline | 3 | |
| Flag for management after N overdue days | 7 | Automatic escalation into the Follow-Up Center |
| KPI weights | 15 / 15 / 30 / 20 / 20 | Meeting, MOM, decision, task, on-time. Weights are re-scaled when a rate has nothing due |

## 5. Academic years and semesters

*Administration → Advanced data administration → Academic years / Semesters*

The year **2026-2027** is created with default semesters: Fall from 14 Sep 2026 to 31 Jan 2027, and Spring from 1 Feb to 30 Jun 2027. **Correct these dates to the official calendar.** Before each new year, add the academic year with its semesters and tick *current year*.

## 6. Audit log — سجل التدقيق

*Administration → Audit log* is append-only. It lists:

* every login (optional filter);
* MOM submissions and approvals;
* decision registrations and cancellations;
* task assignments, evidence, verification, extensions, escalations and reassignments;
* document uploads;
* report exports;
* user and membership changes.

Each entry records who, when, the action and the linked record.

## 7. Demo data

Sample records are tagged **DEMO**. Remove them with `python manage.py purge_demo` (see DEPLOYMENT.md). The command never touches real records.

## 8. Advanced data administration

`/django-admin/` gives the System Administrator raw access to all tables, for corrections. Prefer the normal screens: they apply the workflow rules and write the audit log, and the raw tables do neither.
