function functionName(param1, param2=666, ...kwargs) {
    return true;
}

function functionNameObjects({a, b: B}, c) {
    return true;
}

function _stringLength(s) {
	return s.length;
}
const stringLength = _stringLength;
