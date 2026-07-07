#include "mcwf/common.hpp"

std::string trim(const std::string& s) {
    const char* ws = " \t\r\n";
    size_t a = s.find_first_not_of(ws);
    if (a == std::string::npos) {
        return "";
    }
    size_t b = s.find_last_not_of(ws);
    return s.substr(a, b - a + 1);
}

bool parseBool(const std::string& v) {
    std::string s = trim(v);
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s == "1" || s == "true" || s == "yes" || s == "on";
}

std::vector<std::string> split(const std::string& s, char delim) {
    std::vector<std::string> out;
    std::stringstream ss(s);
    std::string part;
    while (std::getline(ss, part, delim)) {
        out.push_back(trim(part));
    }
    return out;
}

int floorDiv(int a, int b) {
    int q = a / b;
    int r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) {
        --q;
    }
    return q;
}

int clampInt(int v, int lo, int hi) {
    return std::max(lo, std::min(hi, v));
}

double clampDouble(double v, double lo, double hi) {
    return std::max(lo, std::min(hi, v));
}

std::string mcName(const std::string& raw) {
    std::string s = trim(raw);
    if (s.empty()) {
        return "minecraft:stone";
    }
    if (s.find(':') == std::string::npos) {
        return "minecraft:" + s;
    }
    return s;
}
