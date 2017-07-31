export function ClassName(x) {}
ClassName.staticProp = "Static Property";
ClassName.staticMethod = function(y) { };

ClassName.prototype = { constructor: ClassName };
ClassName.prototype.method = function(z) { };
ClassName.prototype.prop = "Property";

var instance = new ClassName();
instance.method();
