# Control Flow Syntax

## If / Else
```peoplecode
If condition Then
   [statement_list_1;]
[Else
   [statement_list_2;]]
End-If;
```
Example:
```peoplecode
If DeleteRow(RECORD.BUS_EXPENSE_PER, &L1_ROW, RECORD.BUS_EXPENSE_DTL, &L2_ROW) Then
   WinMessage("Row deleted.");
Else
   WinMessage("Sorry -- couldn't delete that row.");
End-If;
```

## Evaluate
```peoplecode
Evaluate left_term
   When [relop_1] right_term_1
      [statement_list;]
   [When [relop_n] right_term_n
      [statement_list;]]
   [When-other
      [statement_list;]]
End-Evaluate;
```
Example:
```peoplecode
Evaluate &USE_FREQUENCY
When = "never"
   &PROD_USE_FREQ = 0;
   Break;
When = "sometimes"
   &PROD_USE_FREQ = 1;
   Break;
When = "frequently"
   &PROD_USE_FREQ = 2;
   Break;
When-Other
   Error "Unexpected value assigned to &USE_FREQUENCY.";
End-Evaluate;
```
Rule: add `Break;` when only one `When` branch should execute.

## For
```peoplecode
For count = expression1 To expression2 [Step i]
   statement_list;
End-For;
```
Example:
```peoplecode
&MAX = 10;
Local integer &COUNT;
For &COUNT = 1 To &MAX;
   WinMessage("Executing statement list, count = " | &COUNT);
End-For;
```

## Repeat
```peoplecode
Repeat
   statement_list;
Until logical_expression;
```

## While
```peoplecode
While logical_expression
   statement_list;
End-While;
```
Example:
```peoplecode
Local SQL &SQL = CreateSQL("%Insert(:1)");
While (some condition)
   . . .Setup &Rec
   &Sql.Execute(&Rec);
End-While;
```
Note: `some condition` and setup lines are placeholders from the guide example.

## Loop-control notes
- `Break;` exits current loop.
- `Continue;` moves to next iteration (where used in your runtime version).
- `Return;` exits function scope.
