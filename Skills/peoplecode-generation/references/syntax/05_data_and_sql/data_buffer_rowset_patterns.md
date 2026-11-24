# Data Buffer and Rowset Patterns

## Core object hierarchy
- `Rowset -> Row -> Record -> Field`

## Initialization pattern
```peoplecode
Local Rowset  &VET_SCROLL, &OWNER_SCROLL, &PET_SCROLL, &VISIT_SCROLL;
&VET_SCROLL = GetLevel0();
&OWNER_SCROLL = &VET_Scroll.GetRow(1).GetRowSet(Scroll.OWNER);
&PET_SCROLL = &OWNER_Scroll.GetRow(2).GetRowSet(Scroll.PET);
&VISIT_SCROLL = &PET_Scroll.GetRow(2).GetRowSet(Scroll.VISIT);
```

## Select method syntax
```peoplecode
Select([parmlist], RECORD.selrecord [, wherestr, bindvars]);
```

## Select example
```peoplecode
Local Rowset &BUS_EXPENSE_PER;
&BUS_EXPENSE_PER = GetRowset(SCROLL.BUS_EXPENSE_PER);
&BUS_EXPENSE_PER.Select(RECORD.BUS_EXPENSE_VW,
"WHERE SETID = :1 and CUST_ID = :2", SETID, CUST_ID);
```
Note: the PDF-extracted text appears with a misspelling (`BUS_EXPSNESE_PER`) in one place; use the canonical `BUS_EXPENSE_PER`.

## Rules
- Use `%KeyEqual` when selecting into dependent rowsets to preserve key matching.
- Select records can be narrower than default scroll records for performance.
- For single-row fetches, consider `SelectByKey` on record class where applicable.
