# Class Definition Full Examples

## Complete class example (from guide, cleaned)
```peoplecode
class StringBuffer
   method StringBuffer(&InitialValue As string);
   method Append(&New As string) returns StringBuffer; // allows &X.Append("this").Append("that").Append("and this")
   method Reset();
   property string Value get set;
   property integer Length readonly;
   property integer MaxLength;
private
   instance array of string &Pieces;
end-class;

method StringBuffer
   /+ &InitialValue as String, +/
   &Pieces = CreateArray(&InitialValue);
   &MaxLength = 2147483647; // default maximum size
   &Length = Len(&InitialValue);
end-method;

method Reset
   &Pieces.Len = 0;
   &Length = 0;
end-method;

method Append
   /+ &New as String +/
   Local integer &TempLength = &Length + Len(&New);
   If &Length > &MaxLength Then
      throw CreateException(0, 0, "Maximum size of StringBuffer exceeded(" | &MaxLength | ")");
   End-If;
   &Length = &TempLength;
   &Pieces.Push(&New);
   return %This;
end-method;

get Value
   /+ Returns String +/
   Local string &Temp = &Pieces.Join("", "", "", &Length);
   /* collapse array now */
   &Pieces.Len = 1;
   &Pieces[1] = &Temp; /* start out with this combo string */
   Return &Temp;
end-get;

set Value
   /+ &NewValue as String +/
   /* Ditch our current value */
   &Pieces.Len = 1;
   &Pieces[1] = &NewValue; /* start out with this string */
   &Length = Len(&NewValue);
end-set;
```

## Usage pattern
```peoplecode
Local StringBuffer &S = create StringBuffer("");
&S.Append(&line);
/* read full value via property */
&text = &S.Value;
```

## Class rules
- Use `end-class;`, `end-method;`, `end-get;`, `end-set;` consistently.
- Prefer simple properties when getter/setter logic is not required.
- Use `%This` for fluent/instance-return patterns where appropriate.
