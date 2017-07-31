export class ClassName {
    static staticProp = "Static Property";
    static staticMethod(y) { }

	constructor(x) { }
    method(z) { }
    prop = "Property";
}
var instance = new ClassName();
instance.method();
