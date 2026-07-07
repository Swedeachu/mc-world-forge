#pragma once

#include <string>
#include <vector>

// Small string / numeric helpers shared across the generator.

std::string trim(const std::string& s);

bool parseBool(const std::string& v);

std::vector<std::string> split(const std::string& s, char delim);

int floorDiv(int a, int b);

int clampInt(int v, int lo, int hi);

double clampDouble(double v, double lo, double hi);

std::string mcName(const std::string& raw);
