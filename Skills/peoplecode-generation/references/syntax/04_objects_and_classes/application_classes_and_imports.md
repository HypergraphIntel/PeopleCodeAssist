# Imports and Application Classes

## Import pattern
```peoplecode
Import PackageName:ClassName;
```

## Typed declaration + creation pattern
```peoplecode
Import EOCC:CCI_CRM;
&CCI = Create EOCC:CCI_CRM();
```

## CreateObject dynamic pattern
```peoplecode
&obj = CreateObject("PT_BRANDING:BrandingBase");
```

## Method invocation
```peoplecode
&CCI.Validate(&Card);
&CCI.Submit(&Card);
```

## Extracted import/application class examples
(Page 256)
```peoplecode
Import EOCC:CCI_CRM;
&CCI = Create EOCC:CCI_CRM();
&CCI.Validate(&Card);
&CCI.Submit(&Card);
```

(Page 262)
```peoplecode
import PT_BRANDING:HeaderLinkBase;
import PT_BRANDING:HeaderLinkHP;
Local PT_BRANDING:HeaderLinkBase &objHLBase;
Local PT_BRANDING:HeaderLinkHP &objHeaderLink;
```

## Rules
- Place all `Import` statements at the very top of the PeopleCode program, before any variable declarations or executable statements.
- Keep imports grouped together as the first lines of the file/code section.
- Prefer explicit typed local declarations for reliable editor assistance and safer method/property calls.
- Use `Import` + `Create Package:Class()` for strongly-typed class creation.
- Use dynamic `CreateObject(...)` only when runtime type indirection is required.
