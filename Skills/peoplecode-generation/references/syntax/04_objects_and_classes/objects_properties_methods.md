# Objects, Properties, Methods

## Dot notation
```peoplecode
Object.Property = Value;
Object.Method([params]);
```

## Proven examples
```peoplecode
&MYFIELD.Visible = False;
&X = &MYFIELD.Value;

&FIELD = &RECORD.GetField(FIELD.EMPLID);
&FIELD = &RECORD.EMPLID; /* default method shortcut */

&ROW = &ROWSET.GetRow(5);
&ROW = &ROWSET(5); /* default method shortcut */
```

## Rules
- Instantiate object references before method/property access.
- Use explicit method form where default-method shorthand is ambiguous.
- When field name collides with a record property name, use explicit `GetField`.
