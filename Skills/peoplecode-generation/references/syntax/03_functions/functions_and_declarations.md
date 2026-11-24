# Functions and Declarations

## Definition syntax
```peoplecode
Function name[(paramlist)] [Returns data_type]
   [statements]
End-function
```
Note: case is not significant in PeopleCode keywords; some examples use `End-Function`.
Example:
```peoplecode
Function AddNumbers(&A As Number, &B As Number) Returns Number
   Local Number &Sum;
   &Sum = &A + &B;
   Return &Sum;
End-Function;
```

## Call syntax
```peoplecode
function_name([param_list]);
```
Example:
```peoplecode
Local Number &Total;
&Total = AddNumbers(10, 5);

WinMessage("Done.");
```

## External declaration pattern
```peoplecode
Declare Function FunctionName PeopleCode FUNCLIB_REC.FIELD EventName;
```

## Example declaration
```peoplecode
Declare Function UpdatePSLOCK PeopleCode FUNCLIB_NODES.MSGNODENAME FieldFormula;
```

## External PeopleCode function (complete usage)
Definition (in `FUNCLIB_MYUTIL.FIELDNAME`, `FieldFormula`):
```peoplecode
Function CalcNetAmount(&Gross As Number, &Tax As Number) Returns Number
   Return &Gross - &Tax;
End-Function;
```

Caller:
```peoplecode
Declare Function CalcNetAmount PeopleCode FUNCLIB_MYUTIL.FIELDNAME FieldFormula;
Local Number &Net;
&Net = CalcNetAmount(1000, 180);
```

## Return-value usage
```peoplecode
&RESULT = Product(&RAISE_PERCENT, .01, EMPL_SALARY);
```

## Rules
- External PeopleCode functions must be declared before use.
- `Declare Function` target must match the function-library record/field/event where the function is defined.
- Parameters to internal/external PeopleCode functions are passed by reference.
- If a function name collides with a built-in, the user-defined function takes precedence (avoid this).
- Capture optional boolean return values where behavior matters.

## Source sections
- Function Definitions / Declarations / Calls / Return Values (Chapter 2)
