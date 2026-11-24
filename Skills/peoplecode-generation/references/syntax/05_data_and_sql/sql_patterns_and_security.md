# SQL Patterns and Security

## SQL injection vulnerable pattern (guide example)
```peoplecode
rem Retrieve user input from the name field;
&UserInput = GetField(Field.NAME).Value;
SQLExec("SELECT NAME, PHONE FROM PS_INFO WHERE NAME='"
| &UserInput | "'", &Name, &Phone);
```

## Safer pattern using `Quote` (guide example)
```peoplecode
SQLExec("SELECT NAME, PHONE FROM PS_INFO WHERE NAME='" | Quote(&UserInput) | "'", &Name, &Phone);
```

## SQL object reuse pattern (guide example)
```peoplecode
While (some condition)
   . . .set up &Rec
   SQLExec("%Insert(:1)", &rec);
End-while;

Local SQL &SQL = CreateSQL("%Insert(:1)");
While (some condition)
. . .Setup &Rec
&Sql.Execute(&Rec);
end-while;
```

## Rules
- Avoid concatenating unvalidated user input into SQL.
- Prefer `CreateSQL`/cursor reuse in loops over repeated `SQLExec` parsing.
- Use `Quote(...)` when dynamic string SQL cannot be avoided.
