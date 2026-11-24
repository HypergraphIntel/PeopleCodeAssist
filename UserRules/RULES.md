PeopleCode conventions (PeopleTools 8.63):
- SQLExec/SQL.Fetch outputs bind by reference: read into Locals, then assign to properties/instance vars after -- never bind a property directly.
- NumberToString uses PeopleSoft format %<width>[.<decimals>][RTtv], not printf: "%04d" corrupts the value; use "%04".
- Pick the right row read: SelectByKey (every key incl. EFFDT), SelectByKeyEffDt(&date) (as-of, EFFDT unset), or %SelectAll via SQLExec for a specific row by non-full-key fields.
- SaveEdit/SavePreChange/SavePostChange fire for every row on every save, changed or not -- guard with GetRow().IsChanged/IsNew/IsDeleted before acting.
- Import every Application Class used, even inside App Engine actions and other classes in the same package.
- Treat delivered (non-custom-prefixed) objects as read-only unless told otherwise; clone/extend rather than editing in place.
