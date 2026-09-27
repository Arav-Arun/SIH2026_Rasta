[out:json][timeout:60];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|service)$"](25.560,91.875,25.585,91.900);
  nwr["amenity"~"^(hospital|clinic|pharmacy|marketplace)$"](25.560,91.875,25.585,91.900);
  nwr["building"="warehouse"](25.560,91.875,25.585,91.900);
);
out body center;
>;
out skel qt;
